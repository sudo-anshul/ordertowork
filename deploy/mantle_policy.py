"""Generate the Mantle inference policy; never makes AWS calls or creates keys."""

import argparse
import json
import re
from pathlib import Path

DEFAULT_MODEL = "qwen.qwen3-235b-a22b-2507"


def mantle_policy(
    account: str, region: str, model: str = DEFAULT_MODEL, project: str = "default"
) -> dict:
    if not re.fullmatch(r"[0-9]{12}", account):
        raise ValueError("Expected a 12-digit AWS account ID")
    if not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-[0-9]", region):
        raise ValueError("Expected an AWS region")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", model):
        raise ValueError("Expected one exact Mantle model ID without wildcards")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", project):
        raise ValueError("Expected one exact Mantle project ID without wildcards")
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "InvokeSelectedMantleModelOnly",
                "Effect": "Allow",
                "Action": "bedrock-mantle:CreateInference",
                "Resource": f"arn:aws:bedrock-mantle:{region}:{account}:project/{project}",
                "Condition": {"StringEquals": {"bedrock-mantle:Model": model}},
            },
            {
                "Sid": "UseRegionalShortTermMantleTokens",
                "Effect": "Allow",
                "Action": "bedrock-mantle:CallWithBearerToken",
                # AWS defines no resource type for this permission-only action.
                "Resource": "*",
                "Condition": {
                    "StringEquals": {
                        "bedrock-mantle:BearerTokenType": "SHORT_TERM",
                        "aws:RequestedRegion": region,
                    }
                },
            },
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--project", default="default")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        policy = mantle_policy(args.account, args.region, args.model, args.project)
    except ValueError as error:
        parser.error(str(error))
    args.output.write_text(json.dumps(policy, indent=2) + "\n")
    print("Wrote scoped Mantle policy; no AWS calls or credentials created.")


if __name__ == "__main__":
    main()
