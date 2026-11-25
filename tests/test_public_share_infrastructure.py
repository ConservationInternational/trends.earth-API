"""CloudFront provisioning without contacting AWS or changing live resources."""

from unittest.mock import Mock

from botocore.exceptions import ClientError
import pytest

from scripts.setup import setup_public_shares_infrastructure as infrastructure


@pytest.fixture
def provisioner(monkeypatch):
    session = Mock()
    monkeypatch.setattr(infrastructure.boto3, "Session", Mock(return_value=session))
    instance = infrastructure.Provisioner(
        infrastructure.parse_args(["--environment", "staging", "--skip-github"])
    )
    instance.outputs["alb_dns"] = "origin.example.elb.amazonaws.com"
    instance.api_role = "api-role"
    return instance


@pytest.mark.parametrize("existing", [False, True])
def test_cdn_create_or_update_persists_outputs_and_uses_regional_certificate(
    provisioner, monkeypatch, existing
):
    provisioner.apply = True
    cfn = Mock()
    provisioner.session.client.return_value = cfn
    certificate = Mock(return_value="arn:aws:acm:us-east-1:123:certificate/viewer")
    monkeypatch.setattr(provisioner, "cdn_certificate", certificate)
    result = {
        "Stacks": [
            {
                "Outputs": [
                    {"OutputKey": "DistributionId", "OutputValue": "E123"},
                    {
                        "OutputKey": "DomainName",
                        "OutputValue": "example.cloudfront.net",
                    },
                ]
            }
        ]
    }
    missing = ClientError(
        {"Error": {"Code": "ValidationError", "Message": "does not exist"}},
        "DescribeStacks",
    )
    cfn.describe_stacks.side_effect = [result if existing else missing, result]
    provisioner.ensure_cdn()
    create_or_update = cfn.update_stack if existing else cfn.create_stack
    parameters = {
        item["ParameterKey"]: item["ParameterValue"]
        for item in create_or_update.call_args.kwargs["Parameters"]
    }
    assert parameters == {
        "ViewerHost": "viewer-staging.trends.earth",
        "AlbDomainName": "origin.example.elb.amazonaws.com",
        "CertificateArn": certificate.return_value,
        "ApiRoleName": "api-role",
    }
    cfn.get_waiter.return_value.wait.assert_called_once()
    assert provisioner.outputs["cdn_distribution"] == "E123"
    provisioner.ensure_dns()
    assert any("example.cloudfront.net" in step for step in provisioner.manual_steps)


def test_cdn_plan_does_not_create_certificates_or_deploy(provisioner):
    provisioner.ensure_cdn()
    provisioner.session.client.assert_not_called()


def test_pending_certificate_stops_before_cdn_deployment_and_explains_dns(
    provisioner, capsys
):
    provisioner.apply = True
    acm = Mock()
    provisioner.session.client.return_value = acm
    acm.get_paginator.return_value.paginate.return_value = [
        {"CertificateSummaryList": []}
    ]
    arn = "arn:aws:acm:us-east-1:123:certificate/viewer"
    acm.request_certificate.return_value = {"CertificateArn": arn}
    acm.describe_certificate.return_value = {
        "Certificate": {
            "Status": "PENDING_VALIDATION",
            "DomainValidationOptions": [
                {
                    "ResourceRecord": {
                        "Name": "_validation.viewer.example",
                        "Value": "_validation.acm.example",
                    }
                }
            ],
        }
    }
    with pytest.raises(SystemExit, match="Complete DNS validation"):
        provisioner.ensure_cdn()
    assert (
        "_validation.viewer.example CNAME _validation.acm.example"
        in capsys.readouterr().out
    )
    acm.create_stack.assert_not_called()


def test_cdn_existing_stack_no_changes_is_idempotent(provisioner, monkeypatch):
    provisioner.apply = True
    monkeypatch.setattr(provisioner, "cdn_certificate", lambda: "certificate")
    cfn = provisioner.session.client.return_value
    cfn.describe_stacks.return_value = {
        "Stacks": [
            {
                "Outputs": [
                    {"OutputKey": "DistributionId", "OutputValue": "E123"},
                    {
                        "OutputKey": "DomainName",
                        "OutputValue": "example.cloudfront.net",
                    },
                ]
            }
        ]
    }
    cfn.update_stack.side_effect = ClientError(
        {
            "Error": {
                "Code": "ValidationError",
                "Message": "No updates are to be performed",
            }
        },
        "UpdateStack",
    )
    provisioner.ensure_cdn()
    cfn.get_waiter.assert_not_called()
    assert provisioner.outputs["cdn_distribution"] == "E123"


@pytest.mark.parametrize(
    "names,valid",
    [
        (["viewer-staging.trends.earth"], True),
        (["*.trends.earth"], True),
        (["viewer.trends.earth"], False),
        (["*.earth"], False),
    ],
)
def test_issued_certificate_must_cover_the_viewer_host(provisioner, names, valid):
    arn = "arn:aws:acm:us-east-1:123:certificate/viewer"
    provisioner.args.cdn_certificate_arn = arn
    acm = provisioner.session.client.return_value
    acm.describe_certificate.return_value = {
        "Certificate": {"Status": "ISSUED", "SubjectAlternativeNames": names}
    }
    if valid:
        assert provisioner.cdn_certificate() == arn
    else:
        with pytest.raises(SystemExit, match="does not cover"):
            provisioner.cdn_certificate()
