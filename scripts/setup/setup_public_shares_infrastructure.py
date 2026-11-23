#!/usr/bin/env python3
"""Provision AWS and GitHub prerequisites for expiring public web-map shares.

The script is idempotent and only prints a plan unless ``--apply`` is given.
It never enables ``PUBLIC_SHARES_ENABLED``; that remains a manual release step
after the live acceptance test passes.

Example:
    poetry run python scripts/setup/setup_public_shares_infrastructure.py \\
        --environment staging --viewer-host share-staging.trends.earth
    poetry run python scripts/setup/setup_public_shares_infrastructure.py \\
        --environment staging --viewer-host share-staging.trends.earth --apply
"""

import argparse
import fnmatch
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys

import boto3
from botocore.exceptions import ClientError

REPO_ROOT = Path(__file__).resolve().parents[2]
TILER_TEMPLATE = REPO_ROOT / "cloud_functions" / "titiler_cogs" / "template.yaml"
SAM_BUILD_DIR = REPO_ROOT / ".aws-sam" / "public-share-tiler"
GITHUB_REPO = "ConservationInternational/trends.earth-API"
VIEWER_REPOSITORIES = ("trendsearth-public-viewer", "trendsearth-public-viewer-gateway")
DEFAULT_PORTS = {"production": 8080, "staging": 8081}
API_TARGET_GROUPS = {
    "production": "trendsearth-api-tg",
    "staging": "trendsearth-api-staging",
}
API_PORTS = {"production": 3001, "staging": 3002}
# Shares expire at most 90 days after publication and drafts expire after one
# day, so this only removes objects that cleanup failed to delete.
CURRENT_OBJECT_BACKSTOP_DAYS = 100
INCOMPLETE_UPLOAD_DAYS = 2
TAGS = {"Project": "trends.earth", "Component": "public-web-map-sharing"}


class Provisioner:
    def __init__(self, args):
        self.args = args
        self.apply = args.apply
        session_args = {"region_name": args.region}
        if args.profile:
            session_args["profile_name"] = args.profile
        self.session = boto3.Session(**session_args)
        self.region = args.region
        self.env = args.environment
        self.account_id = None
        self.outputs = {}
        self.manual_steps = []

    def client(self, name):
        return self.session.client(name)

    def step(self, title):
        print(f"\n== {title}")

    def change(self, description, action):
        if self.apply:
            print(f"  + {description}")
            return action()
        print(f"  [plan] {description}")
        return None

    def ok(self, message):
        print(f"  = {message}")

    def tags(self):
        return {**TAGS, "Environment": self.env}

    # ------------------------------------------------------------------
    # Preflight
    # ------------------------------------------------------------------
    def preflight(self):
        self.step("Preflight")
        identity = self.client("sts").get_caller_identity()
        self.account_id = identity["Account"]
        self.ok(f"AWS caller {identity['Arn']} in {self.region}")
        if not self.args.skip_github:
            if not shutil.which("gh"):
                sys.exit("gh CLI is required (or pass --skip-github)")
            auth = subprocess.run(
                ["gh", "auth", "status"],  # noqa: S607
                capture_output=True,
                check=False,
            )
            if auth.returncode:
                sys.exit("gh is not authenticated; run `gh auth login`")
            self.gh(["api", f"repos/{GITHUB_REPO}/environments/{self.env}"])
            self.ok(f"GitHub environment {GITHUB_REPO}/{self.env} exists")
        if not self.args.skip_tiler:
            for tool in ("docker", self.args.sam_cli):
                if not shutil.which(tool):
                    sys.exit(
                        f"{tool} is required to build the tiler (or pass --skip-tiler)"
                    )
        self.bucket = self.args.bucket or (
            f"trendsearth-public-shares-{self.env}-{self.account_id}"
        )
        self.prefix = self.args.prefix.strip("/")
        self.port = self.args.viewer_port or DEFAULT_PORTS[self.env]
        self.origin = f"https://{self.args.viewer_host}"
        self.api_role = self.resolve_api_role()
        self.ok(f"API runtime role: {self.api_role}")

    def resolve_api_role(self):
        if self.args.api_role_name:
            return self.args.api_role_name
        profile = self.client("iam").get_instance_profile(
            InstanceProfileName=self.args.instance_profile
        )
        roles = profile["InstanceProfile"]["Roles"]
        if len(roles) != 1:
            sys.exit(f"Expected one role in {self.args.instance_profile}")
        return roles[0]["RoleName"]

    # ------------------------------------------------------------------
    # S3
    # ------------------------------------------------------------------
    def lifecycle(self):
        prefix = f"{self.prefix}/"
        return {
            "Rules": [
                {
                    "ID": "PublicShareOrphanBackstop",
                    "Status": "Enabled",
                    "Filter": {"Prefix": prefix},
                    "Expiration": {"Days": CURRENT_OBJECT_BACKSTOP_DAYS},
                    "NoncurrentVersionExpiration": {"NoncurrentDays": 1},
                    "AbortIncompleteMultipartUpload": {
                        "DaysAfterInitiation": INCOMPLETE_UPLOAD_DAYS
                    },
                },
                {
                    "ID": "PublicShareDeleteMarkers",
                    "Status": "Enabled",
                    "Filter": {"Prefix": prefix},
                    "Expiration": {"ExpiredObjectDeleteMarker": True},
                },
            ]
        }

    def bucket_policy(self):
        arn = f"arn:aws:s3:::{self.bucket}"
        return {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "DenyInsecureTransport",
                    "Effect": "Deny",
                    "Principal": "*",
                    "Action": "s3:*",
                    "Resource": [arn, f"{arn}/*"],
                    "Condition": {"Bool": {"aws:SecureTransport": "false"}},
                }
            ],
        }

    def ensure_bucket(self):
        self.step(f"Private share bucket s3://{self.bucket}")
        s3 = self.client("s3")
        try:
            s3.head_bucket(Bucket=self.bucket, ExpectedBucketOwner=self.account_id)
            self.ok("bucket exists")
            exists = True
        except ClientError as error:
            if error.response["Error"]["Code"] not in ("404", "NoSuchBucket"):
                raise
            exists = False

        if not exists:

            def create():
                kwargs = {
                    "Bucket": self.bucket,
                    "ObjectOwnership": "BucketOwnerEnforced",
                }
                if self.region != "us-east-1":
                    kwargs["CreateBucketConfiguration"] = {
                        "LocationConstraint": self.region
                    }
                s3.create_bucket(**kwargs)
                s3.get_waiter("bucket_exists").wait(Bucket=self.bucket)

            self.change("create bucket", create)

        # These settings are idempotent, so reapply them to correct drift.
        self.change(
            "block all public access",
            lambda: s3.put_public_access_block(
                Bucket=self.bucket,
                PublicAccessBlockConfiguration={
                    "BlockPublicAcls": True,
                    "IgnorePublicAcls": True,
                    "BlockPublicPolicy": True,
                    "RestrictPublicBuckets": True,
                },
            ),
        )
        self.change(
            "enforce bucket-owner object ownership (ACLs disabled)",
            lambda: s3.put_bucket_ownership_controls(
                Bucket=self.bucket,
                OwnershipControls={
                    "Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]
                },
            ),
        )
        self.change(
            "default SSE-S3 encryption",
            lambda: s3.put_bucket_encryption(
                Bucket=self.bucket,
                ServerSideEncryptionConfiguration={
                    "Rules": [
                        {
                            "ApplyServerSideEncryptionByDefault": {
                                "SSEAlgorithm": "AES256"
                            },
                            "BucketKeyEnabled": True,
                        }
                    ]
                },
            ),
        )
        self.change(
            "enable versioning (vector promotion copies an exact version)",
            lambda: s3.put_bucket_versioning(
                Bucket=self.bucket, VersioningConfiguration={"Status": "Enabled"}
            ),
        )
        self.change(
            "deny non-TLS access",
            lambda: s3.put_bucket_policy(
                Bucket=self.bucket, Policy=json.dumps(self.bucket_policy())
            ),
        )
        self.change(
            f"lifecycle backstop: expire after {CURRENT_OBJECT_BACKSTOP_DAYS} days, "
            f"noncurrent after 1 day, abort uploads after "
            f"{INCOMPLETE_UPLOAD_DAYS} days",
            lambda: s3.put_bucket_lifecycle_configuration(
                Bucket=self.bucket, LifecycleConfiguration=self.lifecycle()
            ),
        )
        self.change(
            "tag bucket",
            lambda: s3.put_bucket_tagging(
                Bucket=self.bucket,
                Tagging={
                    "TagSet": [{"Key": k, "Value": v} for k, v in self.tags().items()]
                },
            ),
        )

    # ------------------------------------------------------------------
    # IAM
    # ------------------------------------------------------------------
    def api_policy(self):
        bucket_arn = f"arn:aws:s3:::{self.bucket}"
        return {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "ListSharePrefix",
                    "Effect": "Allow",
                    "Action": [
                        "s3:ListBucket",
                        "s3:ListBucketVersions",
                        "s3:ListBucketMultipartUploads",
                    ],
                    "Resource": bucket_arn,
                    "Condition": {
                        "StringLike": {"s3:prefix": [self.prefix, f"{self.prefix}/*"]}
                    },
                },
                {
                    "Sid": "ManageShareObjects",
                    "Effect": "Allow",
                    "Action": [
                        "s3:GetObject",
                        "s3:GetObjectVersion",
                        "s3:PutObject",
                        "s3:DeleteObject",
                        "s3:DeleteObjectVersion",
                        "s3:AbortMultipartUpload",
                        "s3:ListMultipartUploadParts",
                    ],
                    "Resource": f"{bucket_arn}/{self.prefix}/*",
                },
            ],
        }

    def martin_policy(self):
        bucket_arn = f"arn:aws:s3:::{self.bucket}"
        vector_prefix = f"{self.prefix}/vector/"
        return {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "ListValidatedVectors",
                    "Effect": "Allow",
                    "Action": "s3:ListBucket",
                    "Resource": bucket_arn,
                    "Condition": {
                        "StringLike": {
                            "s3:prefix": [vector_prefix, f"{vector_prefix}*"]
                        }
                    },
                },
                {
                    "Sid": "ReadValidatedVectors",
                    "Effect": "Allow",
                    "Action": "s3:GetObject",
                    "Resource": f"{bucket_arn}/{vector_prefix}*",
                },
            ],
        }

    def ensure_api_role_policy(self):
        name = f"TrendsEarthPublicShares-{self.env}"
        self.step(f"API/worker S3 policy {name} on role {self.api_role}")
        iam = self.client("iam")
        self.change(
            "put least-privilege share-prefix policy",
            lambda: iam.put_role_policy(
                RoleName=self.api_role,
                PolicyName=name,
                PolicyDocument=json.dumps(self.api_policy()),
            ),
        )

    def ensure_martin_user(self, github_secrets):
        user = f"trendsearth-public-shares-martin-{self.env}"
        self.step(f"Martin read-only IAM user {user}")
        iam = self.client("iam")
        try:
            iam.get_user(UserName=user)
            self.ok("user exists")
            keys = iam.list_access_keys(UserName=user)["AccessKeyMetadata"]
        except iam.exceptions.NoSuchEntityException:
            self.change(
                "create user",
                lambda: iam.create_user(
                    UserName=user,
                    Tags=[{"Key": k, "Value": v} for k, v in self.tags().items()],
                ),
            )
            keys = []
        self.change(
            "put vector-prefix read-only policy",
            lambda: iam.put_user_policy(
                UserName=user,
                PolicyName="ReadValidatedPublicShareVectors",
                PolicyDocument=json.dumps(self.martin_policy()),
            ),
        )

        stored = {
            "PUBLIC_SHARES_MARTIN_AWS_ACCESS_KEY_ID",
            "PUBLIC_SHARES_MARTIN_AWS_SECRET_ACCESS_KEY",
        } <= github_secrets
        if stored and keys and not self.args.rotate_martin_key:
            self.ok(
                "access key already stored in GitHub; "
                "pass --rotate-martin-key to replace"
            )
            return
        if len(keys) >= 2:
            sys.exit(
                f"{user} already has two access keys; delete the unused one before "
                "creating a replacement"
            )

        def create_key():
            key = iam.create_access_key(UserName=user)["AccessKey"]
            self.set_secret(
                "PUBLIC_SHARES_MARTIN_AWS_ACCESS_KEY_ID", key["AccessKeyId"]
            )
            self.set_secret(
                "PUBLIC_SHARES_MARTIN_AWS_SECRET_ACCESS_KEY", key["SecretAccessKey"]
            )
            for old in keys:
                self.manual_steps.append(
                    "After the next successful deployment, delete the previous Martin "
                    f"key: aws iam delete-access-key --user-name {user} "
                    f"--access-key-id {old['AccessKeyId']}"
                )

        self.change("create access key and store it only as GitHub secrets", create_key)

    # ------------------------------------------------------------------
    # ECR and GitHub OIDC
    # ------------------------------------------------------------------
    def ensure_ecr(self):
        self.step("Viewer ECR repositories")
        ecr = self.client("ecr")
        lifecycle = {
            "rules": [
                {
                    "rulePriority": 1,
                    "description": "Expire untagged images",
                    "selection": {
                        "tagStatus": "untagged",
                        "countType": "sinceImagePushed",
                        "countUnit": "days",
                        "countNumber": 7,
                    },
                    "action": {"type": "expire"},
                },
                {
                    "rulePriority": 2,
                    "description": "Keep the newest 60 images",
                    "selection": {
                        "tagStatus": "any",
                        "countType": "imageCountMoreThan",
                        "countNumber": 60,
                    },
                    "action": {"type": "expire"},
                },
            ]
        }
        for name in VIEWER_REPOSITORIES:
            try:
                ecr.describe_repositories(repositoryNames=[name])
                self.ok(f"{name} exists")
            except ecr.exceptions.RepositoryNotFoundException:
                self.change(
                    f"create {name}",
                    lambda name=name: ecr.create_repository(
                        repositoryName=name,
                        imageScanningConfiguration={"scanOnPush": True},
                        imageTagMutability="MUTABLE",
                        encryptionConfiguration={"encryptionType": "AES256"},
                        tags=[{"Key": k, "Value": v} for k, v in TAGS.items()],
                    ),
                )
            self.change(
                f"apply image lifecycle policy to {name}",
                lambda name=name: ecr.put_lifecycle_policy(
                    repositoryName=name, lifecyclePolicyText=json.dumps(lifecycle)
                ),
            )

    def ensure_github_oidc_policy(self):
        self.step("GitHub Actions push access to viewer repositories")
        role = self.args.github_oidc_role
        if not role:
            if self.args.skip_github:
                self.ok("skipped (--skip-github and no --github-oidc-role)")
                return
            arn = self.gh(["variable", "get", "AWS_OIDC_ROLE_ARN", "-R", GITHUB_REPO])
            role = arn.strip().rsplit("/", 1)[-1]
        repos = [
            f"arn:aws:ecr:{self.region}:{self.account_id}:repository/{name}"
            for name in VIEWER_REPOSITORIES
        ]
        policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "PushPublicViewerImages",
                    "Effect": "Allow",
                    "Action": [
                        "ecr:BatchCheckLayerAvailability",
                        "ecr:GetDownloadUrlForLayer",
                        "ecr:BatchGetImage",
                        "ecr:InitiateLayerUpload",
                        "ecr:UploadLayerPart",
                        "ecr:CompleteLayerUpload",
                        "ecr:PutImage",
                        "ecr:DescribeRepositories",
                        "ecr:DescribeImages",
                    ],
                    "Resource": repos,
                }
            ],
        }
        iam = self.client("iam")
        self.change(
            f"put GitHubActionsPublicViewerECR policy on {role}",
            lambda: iam.put_role_policy(
                RoleName=role,
                PolicyName="GitHubActionsPublicViewerECR",
                PolicyDocument=json.dumps(policy),
            ),
        )

    # ------------------------------------------------------------------
    # Private TiTiler Lambda
    # ------------------------------------------------------------------
    def ensure_tiler(self):
        stack = f"trendsearth-public-share-tiler-{self.env}"
        self.step(f"Private TiTiler Lambda stack {stack}")
        cfn = self.client("cloudformation")
        try:
            existing = cfn.describe_stacks(StackName=stack)["Stacks"][0]
            self.ok(f"stack exists ({existing['StackStatus']})")
            self.outputs["tiler_arn"] = self.stack_output(existing)
        except ClientError as error:
            if "does not exist" not in str(error):
                raise

        def deploy():
            env = {"SAM_CLI_TELEMETRY": "0"}
            base = [self.args.sam_cli]
            common = ["--region", self.region]
            if self.args.profile:
                common += ["--profile", self.args.profile]
            self.run(
                base
                + [
                    "build",
                    "--template-file",
                    str(TILER_TEMPLATE),
                    "--build-dir",
                    str(SAM_BUILD_DIR),
                ],
                env,
            )
            overrides = {
                "FunctionName": f"trendsearth-public-share-tiler-{self.env}",
                "ShareBucket": self.bucket,
                "SharePrefix": self.prefix,
                "ApiRoleName": self.api_role,
                "ReservedConcurrency": str(self.args.tiler_reserved_concurrency),
            }
            self.run(
                base
                + [
                    "deploy",
                    "--template-file",
                    str(SAM_BUILD_DIR / "template.yaml"),
                    "--stack-name",
                    stack,
                    "--resolve-image-repos",
                    "--resolve-s3",
                    "--capabilities",
                    "CAPABILITY_IAM",
                    "--no-confirm-changeset",
                    "--no-fail-on-empty-changeset",
                    "--parameter-overrides",
                    *[f"{key}={value}" for key, value in overrides.items()],
                    "--tags",
                    *[f"{key}={value}" for key, value in self.tags().items()],
                ]
                + common,
                env,
            )
            result = cfn.describe_stacks(StackName=stack)["Stacks"][0]
            self.outputs["tiler_arn"] = self.stack_output(result)
            self.ok(f"renderer alias {self.outputs['tiler_arn']}")

        self.change(
            "sam build and deploy (image Lambda, IAM-invoked live alias)", deploy
        )

    @staticmethod
    def stack_output(stack):
        for output in stack.get("Outputs", []):
            if output["OutputKey"] == "RendererFunctionArn":
                return output["OutputValue"]
        return None

    # ------------------------------------------------------------------
    # Load balancer routing
    # ------------------------------------------------------------------
    def ensure_load_balancer(self):
        self.step(
            f"ALB routing https://{self.args.viewer_host} -> gateway port {self.port}"
        )
        elb = self.client("elbv2")
        ec2 = self.client("ec2")
        lb = elb.describe_load_balancers(Names=[self.args.load_balancer])[
            "LoadBalancers"
        ][0]
        self.outputs["alb_dns"] = lb["DNSName"]
        listeners = elb.describe_listeners(LoadBalancerArn=lb["LoadBalancerArn"])[
            "Listeners"
        ]
        https = next((item for item in listeners if item["Port"] == 443), None)
        if not https:
            sys.exit(f"{self.args.load_balancer} has no HTTPS listener")
        self.check_certificate(elb, https)

        api_tg = elb.describe_target_groups(Names=[API_TARGET_GROUPS[self.env]])[
            "TargetGroups"
        ][0]
        targets = [
            item["Target"]["Id"]
            for item in elb.describe_target_health(
                TargetGroupArn=api_tg["TargetGroupArn"]
            )["TargetHealthDescriptions"]
        ]
        if not targets:
            sys.exit(f"{API_TARGET_GROUPS[self.env]} has no registered targets to copy")

        tg_name = f"te-share-viewer-{self.env}"
        try:
            tg = elb.describe_target_groups(Names=[tg_name])["TargetGroups"][0]
            self.ok(f"target group {tg_name} exists")
            if tg["Port"] != self.port:
                sys.exit(f"{tg_name} uses port {tg['Port']}, expected {self.port}")
            tg_arn = tg["TargetGroupArn"]
        except elb.exceptions.TargetGroupNotFoundException:
            tg_arn = self.change(
                f"create target group {tg_name} (HTTP {self.port}, /health)",
                lambda: elb.create_target_group(
                    Name=tg_name,
                    Protocol="HTTP",
                    Port=self.port,
                    VpcId=api_tg["VpcId"],
                    TargetType="instance",
                    HealthCheckProtocol="HTTP",
                    HealthCheckPath="/health",
                    Matcher={"HttpCode": "200"},
                    Tags=[{"Key": k, "Value": v} for k, v in self.tags().items()],
                )["TargetGroups"][0]["TargetGroupArn"],
            )
        self.change(
            f"register Swarm targets {', '.join(targets)}",
            lambda: elb.register_targets(
                TargetGroupArn=tg_arn,
                Targets=[{"Id": target, "Port": self.port} for target in targets],
            ),
        )

        rules = elb.describe_rules(ListenerArn=https["ListenerArn"])["Rules"]
        existing = None
        for rule in rules:
            for condition in rule.get("Conditions", []):
                if condition.get(
                    "Field"
                ) == "host-header" and self.args.viewer_host in (
                    condition.get("HostHeaderConfig", {}).get("Values")
                    or condition.get("Values", [])
                ):
                    existing = rule
        if existing:
            self.change(
                f"point existing rule {existing['Priority']} at {tg_name}",
                lambda: elb.modify_rule(
                    RuleArn=existing["RuleArn"],
                    Actions=[{"Type": "forward", "TargetGroupArn": tg_arn}],
                ),
            )
        else:
            used = {
                int(rule["Priority"]) for rule in rules if rule["Priority"].isdigit()
            }
            priority = next(p for p in range(30, 50000) if p not in used)
            self.change(
                f"add host-header rule priority {priority}",
                lambda: elb.create_rule(
                    ListenerArn=https["ListenerArn"],
                    Priority=priority,
                    Conditions=[
                        {
                            "Field": "host-header",
                            "HostHeaderConfig": {"Values": [self.args.viewer_host]},
                        }
                    ],
                    Actions=[{"Type": "forward", "TargetGroupArn": tg_arn}],
                    Tags=[{"Key": k, "Value": v} for k, v in self.tags().items()],
                ),
            )

        self.ensure_ingress(ec2, lb["SecurityGroups"], targets)

    def check_certificate(self, elb, listener):
        acm = self.client("acm")
        certificates = elb.describe_listener_certificates(
            ListenerArn=listener["ListenerArn"]
        )["Certificates"]
        names = []
        for certificate in certificates:
            detail = acm.describe_certificate(
                CertificateArn=certificate["CertificateArn"]
            )
            names += detail["Certificate"].get("SubjectAlternativeNames", [])
        host = self.args.viewer_host
        if not any(
            name == host
            or (
                name.startswith("*.")
                and fnmatch.fnmatch(host, name)
                and host.count(".") == name.count(".")
            )
            for name in names
        ):
            sys.exit(
                f"No HTTPS listener certificate covers {host}; add one in ACM first"
            )
        self.ok(f"listener certificate covers {host}")

    def ensure_ingress(self, ec2, alb_groups, targets):
        instances = ec2.describe_instances(InstanceIds=targets)["Reservations"]
        group_ids = {
            group["GroupId"]
            for reservation in instances
            for instance in reservation["Instances"]
            for group in instance["SecurityGroups"]
        }
        groups = ec2.describe_security_groups(GroupIds=list(group_ids))[
            "SecurityGroups"
        ]

        def allows(group, port):
            return any(
                rule.get("FromPort") == port
                and rule.get("ToPort") == port
                and any(
                    pair["GroupId"] in alb_groups for pair in rule["UserIdGroupPairs"]
                )
                for rule in group["IpPermissions"]
            )

        # Reuse the security group that already admits the ALB to this API.
        api_groups = [group for group in groups if allows(group, API_PORTS[self.env])]
        if not api_groups:
            sys.exit(
                "Could not find the target security group used for ALB->API traffic"
            )
        group = api_groups[0]
        if allows(group, self.port):
            self.ok(f"{group['GroupId']} already admits the ALB on {self.port}")
            return
        self.change(
            f"allow ALB {alb_groups[0]} to reach {group['GroupId']} on TCP {self.port}",
            lambda: ec2.authorize_security_group_ingress(
                GroupId=group["GroupId"],
                IpPermissions=[
                    {
                        "IpProtocol": "tcp",
                        "FromPort": self.port,
                        "ToPort": self.port,
                        "UserIdGroupPairs": [
                            {
                                "GroupId": alb_groups[0],
                                "Description": f"ALB to share viewer ({self.env})",
                            }
                        ],
                    }
                ],
            ),
        )

    def ensure_dns(self):
        self.step(f"DNS for {self.args.viewer_host}")
        target = self.outputs.get("alb_dns")
        if not self.args.route53_zone_id:
            self.ok("no --route53-zone-id given")
            self.manual_steps.append(
                f"Create DNS CNAME {self.args.viewer_host} -> {target} "
                "(trends.earth is not hosted in this account's Route 53)."
            )
            return
        route53 = self.client("route53")
        self.change(
            f"UPSERT CNAME {self.args.viewer_host} -> {target}",
            lambda: route53.change_resource_record_sets(
                HostedZoneId=self.args.route53_zone_id,
                ChangeBatch={
                    "Changes": [
                        {
                            "Action": "UPSERT",
                            "ResourceRecordSet": {
                                "Name": self.args.viewer_host,
                                "Type": "CNAME",
                                "TTL": 300,
                                "ResourceRecords": [{"Value": target}],
                            },
                        }
                    ]
                },
            ),
        )

    # ------------------------------------------------------------------
    # GitHub
    # ------------------------------------------------------------------
    def gh(self, args, stdin=None):
        result = subprocess.run(  # noqa: S603
            ["gh", *args],  # noqa: S607
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            # gh error text never contains secret values because they use stdin.
            sys.exit(f"gh {' '.join(args[:3])} failed: {result.stderr.strip()}")
        return result.stdout

    def github_variables(self):
        if self.args.skip_github:
            return {}
        output = self.gh(
            [
                "variable",
                "list",
                "--env",
                self.env,
                "-R",
                GITHUB_REPO,
                "--json",
                "name,value",
            ]
        )
        return {item["name"]: item["value"] for item in json.loads(output or "[]")}

    def github_secret_names(self):
        if self.args.skip_github:
            return set()
        output = self.gh(
            ["secret", "list", "--env", self.env, "-R", GITHUB_REPO, "--json", "name"]
        )
        return {item["name"] for item in json.loads(output or "[]")}

    def set_variable(self, name, value):
        if self.args.skip_github:
            self.manual_steps.append(f"Set GitHub {self.env} variable {name}={value}")
            return
        self.gh(
            [
                "variable",
                "set",
                name,
                "--env",
                self.env,
                "-R",
                GITHUB_REPO,
                "--body",
                value,
            ]
        )

    def set_secret(self, name, value):
        if self.args.skip_github:
            sys.exit(f"Cannot store secret {name} with --skip-github")
        self.gh(
            ["secret", "set", name, "--env", self.env, "-R", GITHUB_REPO], stdin=value
        )

    def ensure_github_settings(self, variables, secret_names):
        self.step(f"GitHub {self.env} environment variables and secrets")
        desired = {
            "PUBLIC_SHARES_S3_BUCKET": self.bucket,
            "PUBLIC_SHARES_S3_PREFIX": self.prefix,
            "PUBLIC_SHARES_PUBLIC_ORIGIN": self.origin,
            "PUBLIC_SHARES_VIEWER_BASE_URL": f"{self.origin}/share",
            "PUBLIC_VIEWER_PORT": str(self.port),
        }
        tiler_arn = self.outputs.get("tiler_arn")
        if tiler_arn:
            desired["PUBLIC_SHARES_TITILER_LAMBDA_ARN"] = tiler_arn
        elif self.apply and not variables.get("PUBLIC_SHARES_TITILER_LAMBDA_ARN"):
            sys.exit("No TiTiler Lambda ARN available; deploy the tiler first")
        else:
            print("  [plan] set PUBLIC_SHARES_TITILER_LAMBDA_ARN from the SAM output")

        for name, value in desired.items():
            if variables.get(name) == value:
                self.ok(f"{name}={value}")
            else:
                self.change(
                    f"set {name}={value}",
                    lambda n=name, v=value: self.set_variable(n, v),
                )

        if "PUBLIC_SHARES_ENABLED" not in variables:
            self.change(
                "set PUBLIC_SHARES_ENABLED=false (enable manually after acceptance)",
                lambda: self.set_variable("PUBLIC_SHARES_ENABLED", "false"),
            )
        else:
            self.ok(
                f"PUBLIC_SHARES_ENABLED={variables['PUBLIC_SHARES_ENABLED']} "
                "(left unchanged)"
            )

        if (
            "PUBLIC_SHARES_SESSION_SECRET" in secret_names
            and not self.args.rotate_session_secret
        ):
            self.ok(
                "PUBLIC_SHARES_SESSION_SECRET is set "
                "(pass --rotate-session-secret to replace)"
            )
        else:
            self.change(
                "generate PUBLIC_SHARES_SESSION_SECRET (64 random bytes)",
                lambda: self.set_secret(
                    "PUBLIC_SHARES_SESSION_SECRET", secrets.token_urlsafe(64)
                ),
            )

    def mark_infrastructure_ready(self, variables):
        self.step("Enable overlay deployment")
        if variables.get("PUBLIC_SHARES_INFRA_ENABLED") == "true":
            self.ok("PUBLIC_SHARES_INFRA_ENABLED=true")
            return
        self.change(
            "set PUBLIC_SHARES_INFRA_ENABLED=true (deploy viewer, gateway and Martin)",
            lambda: self.set_variable("PUBLIC_SHARES_INFRA_ENABLED", "true"),
        )

    def trigger_deploy(self):
        self.step("CodeDeploy workflow")
        workflow = f"codedeploy_{self.env}.yml"
        command = [
            "workflow",
            "run",
            workflow,
            "-R",
            GITHUB_REPO,
            "--ref",
            self.args.deploy_ref,
            "-f",
            "force_deploy=true",
        ]
        self.change(
            f"dispatch {workflow} on {self.args.deploy_ref}",
            lambda: self.gh(command),
        )
        self.manual_steps.append(
            f"Watch the deployment: gh run list -R {GITHUB_REPO} "
            f"--workflow {workflow} --limit 1"
        )

    # ------------------------------------------------------------------
    @staticmethod
    def run(command, extra_env):
        env = {**os.environ, **extra_env}
        subprocess.run(command, cwd=REPO_ROOT, env=env, check=True)  # noqa: S603

    def execute(self):
        self.preflight()
        variables = self.github_variables()
        secret_names = self.github_secret_names()
        self.ensure_bucket()
        self.ensure_api_role_policy()
        self.ensure_martin_user(secret_names)
        self.ensure_ecr()
        self.ensure_github_oidc_policy()
        if self.args.skip_tiler:
            self.step("Private TiTiler Lambda stack")
            self.ok("skipped (--skip-tiler)")
        else:
            self.ensure_tiler()
        self.ensure_load_balancer()
        self.ensure_dns()
        if not self.args.skip_github:
            self.ensure_github_settings(variables, secret_names)
            self.mark_infrastructure_ready(variables)
            if self.args.trigger_deploy:
                self.trigger_deploy()

        self.manual_steps += [
            (
                f"Confirm https://{self.args.viewer_host}/health returns OK "
                "after deployment."
            ),
            (
                "Run the live sharing acceptance test, then set "
                f"PUBLIC_SHARES_ENABLED=true in the GitHub {self.env} "
                "environment and redeploy."
            ),
        ]
        print("\n== Remaining manual steps")
        for item in self.manual_steps:
            print(f"  - {item}")
        if not self.apply:
            print("\nPlan only. Re-run with --apply to make these changes.")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--environment", "-e", required=True, choices=DEFAULT_PORTS)
    parser.add_argument(
        "--viewer-host",
        required=True,
        help="Public viewer hostname, e.g. share.trends.earth",
    )
    parser.add_argument(
        "--apply", action="store_true", help="Make changes (default: plan)"
    )
    parser.add_argument("--profile", "-p", help="AWS profile")
    parser.add_argument("--region", "-r", default="us-east-1")
    parser.add_argument(
        "--bucket", help="Default: trendsearth-public-shares-<env>-<account>"
    )
    parser.add_argument("--prefix", default="public-shares")
    parser.add_argument(
        "--viewer-port", type=int, help="Default: production 8080, staging 8081"
    )
    parser.add_argument(
        "--instance-profile",
        default="trendsearth-api-docker-swarm-role",
        help="Swarm node instance profile whose role runs the API and workers",
    )
    parser.add_argument("--api-role-name", help="Override the API runtime role")
    parser.add_argument("--github-oidc-role", help="Default: from AWS_OIDC_ROLE_ARN")
    parser.add_argument("--load-balancer", default="trendsearth-api")
    parser.add_argument(
        "--route53-zone-id", help="Create the viewer CNAME in this zone"
    )
    parser.add_argument("--sam-cli", default="sam")
    parser.add_argument("--tiler-reserved-concurrency", type=int, default=20)
    parser.add_argument("--rotate-martin-key", action="store_true")
    parser.add_argument(
        "--rotate-session-secret",
        action="store_true",
        help="Replace the viewer-session key (invalidates active viewer sessions)",
    )
    parser.add_argument("--skip-tiler", action="store_true")
    parser.add_argument("--skip-github", action="store_true")
    parser.add_argument(
        "--trigger-deploy",
        action="store_true",
        help="Dispatch the CodeDeploy workflow after provisioning",
    )
    parser.add_argument("--deploy-ref", default="master")
    args = parser.parse_args(argv)
    if args.trigger_deploy and args.skip_github:
        parser.error("--trigger-deploy requires GitHub access")
    return args


def main(argv=None):
    Provisioner(parse_args(argv)).execute()


if __name__ == "__main__":
    main()
