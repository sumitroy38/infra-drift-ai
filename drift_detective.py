#!/usr/bin/env python3
"""
Drift Detective - a CLI tool that compares Terraform's intended
infrastructure state against what's actually running (via LocalStack
or real AWS), flags drift, explains risk in plain English (Ollama),
and generates ready-to-run Terraform import fixes.
"""

import argparse
import json
import re
import sys

import boto3
import requests


def load_terraform_state(state_path):
    with open(state_path) as f:
        return json.load(f)


def build_expected(tf_state):
    expected = {"s3": set(), "ec2": set(), "iam": set()}
    for resource in tf_state["resources"]:
        if resource["type"] == "aws_s3_bucket":
            for instance in resource["instances"]:
                expected["s3"].add(instance["attributes"]["bucket"])
        elif resource["type"] == "aws_instance":
            for instance in resource["instances"]:
                if instance["attributes"].get("id"):
                    expected["ec2"].add(instance["attributes"]["id"])
        elif resource["type"] == "aws_iam_user":
            for instance in resource["instances"]:
                expected["iam"].add(instance["attributes"]["name"])
    return expected


def build_actual(endpoint_url):
    common_args = dict(
        endpoint_url=endpoint_url,
        aws_access_key_id="test",
        aws_secret_access_key="test",
        region_name="us-east-1",
    )
    session = boto3.session.Session()
    s3 = session.client("s3", **common_args)
    ec2 = session.client("ec2", **common_args)
    iam = session.client("iam", **common_args)

    actual = {"s3": set(), "ec2": set(), "iam": set()}
    actual["s3"] = {b["Name"] for b in s3.list_buckets()["Buckets"]}
    for reservation in ec2.describe_instances()["Reservations"]:
        for inst in reservation["Instances"]:
            if inst["State"]["Name"] != "terminated":
                actual["ec2"].add(inst["InstanceId"])
    actual["iam"] = {u["UserName"] for u in iam.list_users()["Users"]}
    return actual


def safe_name(raw):
    return re.sub(r"[^a-zA-Z0-9_]", "_", raw)


def generate_fix(kind, resource_id):
    tf_name = safe_name(resource_id)
    if kind == "s3":
        return (
            f"terraform import aws_s3_bucket.{tf_name} {resource_id}\n\n"
            f'resource "aws_s3_bucket" "{tf_name}" {{\n'
            f'  bucket = "{resource_id}"\n}}'
        )
    if kind == "ec2":
        return (
            f"terraform import aws_instance.{tf_name} {resource_id}\n\n"
            f'resource "aws_instance" "{tf_name}" {{\n'
            f'  ami           = "REPLACE_WITH_ACTUAL_AMI"  '
            f"# run: aws ec2 describe-instances --instance-ids {resource_id}\n"
            f'  instance_type = "REPLACE_WITH_ACTUAL_TYPE"\n}}'
        )
    if kind == "iam":
        return (
            f"terraform import aws_iam_user.{tf_name} {resource_id}\n\n"
            f'resource "aws_iam_user" "{tf_name}" {{\n'
            f'  name = "{resource_id}"\n}}'
        )
    return ""


def ask_ai_risk(findings, ollama_url, model):
    prompt = (
        "You are a DevOps assistant. Explain the SECURITY AND COST RISK of "
        "each of these cloud infrastructure drift findings, in 1-2 "
        "plain-English sentences each. Do NOT write any code or Terraform "
        "syntax - just the risk explanation.\n\n" + "\n".join(findings)
    )
    try:
        response = requests.post(
            f"{ollama_url}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=60,
        )
        response.raise_for_status()
        return response.json()["response"]
    except requests.exceptions.RequestException as e:
        return f"(AI explanation unavailable - Ollama not reachable: {e})"


def cmd_check(args):
    tf_state = load_terraform_state(args.terraform_state)
    expected = build_expected(tf_state)
    actual = build_actual(args.endpoint_url)

    findings = []
    drift_items = []

    for kind in ["s3", "ec2", "iam"]:
        drifted = actual[kind] - expected[kind]
        missing = expected[kind] - actual[kind]
        for rid in drifted:
            findings.append(f"{kind.upper()} resource running but NOT in Terraform: {rid}")
            drift_items.append((kind, rid))
        for rid in missing:
            findings.append(f"{kind.upper()} resource expected by Terraform but MISSING: {rid}")

    print("=== RAW FINDINGS ===")
    if not findings:
        print("No drift detected across S3, EC2, or IAM.")
        return 0
    for f_item in findings:
        print("-", f_item)

    if not args.no_ai:
        print("\n=== AI RISK EXPLANATION ===")
        print(ask_ai_risk(findings, args.ollama_url, args.model))

    if drift_items:
        print("\n=== SUGGESTED FIXES (template-generated) ===")
        for kind, rid in drift_items:
            print(f"\n--- Fix for {kind.upper()}: {rid} ---")
            print(generate_fix(kind, rid))

    return 1


def main():
    parser = argparse.ArgumentParser(
        prog="drift-detective",
        description="Detect cloud infrastructure drift between Terraform and live state.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser("check", help="Run a drift check")
    check_parser.add_argument(
        "--terraform-state",
        default="terraform/terraform.tfstate",
        help="Path to terraform.tfstate (default: terraform/terraform.tfstate)",
    )
    check_parser.add_argument(
        "--endpoint-url",
        default="http://localhost:4566",
        help="AWS/LocalStack endpoint (default: http://localhost:4566)",
    )
    check_parser.add_argument(
        "--ollama-url",
        default="http://localhost:11434",
        help="Ollama server URL (default: http://localhost:11434)",
    )
    check_parser.add_argument(
        "--model", default="llama3", help="Ollama model name (default: llama3)"
    )
    check_parser.add_argument(
        "--no-ai", action="store_true", help="Skip AI risk explanation (faster, offline-safe)"
    )
    check_parser.set_defaults(func=cmd_check)

    args = parser.parse_args()
    exit_code = args.func(args)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
