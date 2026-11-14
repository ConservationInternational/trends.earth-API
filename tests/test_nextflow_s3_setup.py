"""Tests for Nextflow S3 lifecycle configuration."""

import importlib.util
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "setup"
    / "setup_nextflow_s3_bucket.py"
)
SPEC = importlib.util.spec_from_file_location("setup_nextflow_s3_bucket", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_nextflow_bucket_lifecycle_expires_each_environment_prefix():
    rules = MODULE.lifecycle_configuration()["Rules"]
    expiration_rules = [rule for rule in rules if "Days" in rule["Expiration"]]

    assert {
        rule["Filter"]["Prefix"]: rule["Expiration"]["Days"]
        for rule in expiration_rules
    } == {
        "production/work/": 365,
        "production/state/": 365,
        "staging/work/": 365,
        "staging/state/": 365,
    }
    assert all(
        rule["NoncurrentVersionExpiration"]["NoncurrentDays"] == 1
        for rule in expiration_rules
    )
    assert all(
        rule["AbortIncompleteMultipartUpload"]["DaysAfterInitiation"] == 7
        for rule in expiration_rules
    )
    delete_marker_rules = [
        rule for rule in rules if "ExpiredObjectDeleteMarker" in rule["Expiration"]
    ]
    assert {rule["Filter"]["Prefix"] for rule in delete_marker_rules} == {
        "production/work/",
        "production/state/",
        "staging/work/",
        "staging/state/",
    }
    assert all(
        rule["Expiration"]["ExpiredObjectDeleteMarker"] is True
        for rule in delete_marker_rules
    )
