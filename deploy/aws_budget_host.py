"""Provision one small hackathon host; no load balancer, NAT gateway or managed DB.

Reads the aws_services.py manifest. Writes resource IDs for repeatable operations.
--apply starts hourly billing; preview only discovers the account/VPC/AMI.
"""

import argparse
import json
import time
from pathlib import Path

import boto3
from aws_services import owned, write_manifest
from botocore.config import Config
from botocore.exceptions import ClientError
from mantle_policy import DEFAULT_MODEL, mantle_policy


def launch_instance(ec2, **parameters):
    # Newly created instance profiles take a few seconds to reach EC2. The client
    # token makes a retry idempotent; other launch errors are never hidden.
    for attempt in range(8):
        try:
            return ec2.run_instances(**parameters)
        except ClientError as error:
            detail = error.response["Error"]
            if (
                detail["Code"] != "InvalidParameterValue"
                or "instance profile" not in detail["Message"].lower()
                or attempt == 7
            ):
                raise
            time.sleep(3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--services", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    services = json.loads(args.services.read_text())
    project, region, account = (services[key] for key in ("project", "region", "account_id"))
    if region != "us-east-1":
        parser.error(
            "This priced host setup is limited to us-east-1; recheck costs before changing"
        )
    bucket = services["resource_names"]["bucket"]
    app_env = services.get("app_env", {})
    endpoint = app_env.get("OTW_BEDROCK_ENDPOINT", "runtime")
    if endpoint not in {"runtime", "mantle"}:
        parser.error("OTW_BEDROCK_ENDPOINT must be runtime or mantle")
    mantle_statements = (
        mantle_policy(
            account,
            region,
            app_env.get("OTW_BEDROCK_MODEL_ID", DEFAULT_MODEL),
            app_env.get("OTW_BEDROCK_MANTLE_PROJECT_ID", "default"),
        )["Statement"]
        if endpoint == "mantle"
        else None
    )
    session = boto3.Session(profile_name=args.profile, region_name=region)
    sdk = Config(connect_timeout=5, read_timeout=30, retries={"total_max_attempts": 3})
    if session.client("sts", config=sdk).get_caller_identity()["Account"] != account:
        parser.error("The setup profile belongs to a different account")
    ec2, iam, ecr, ssm = (session.client(name, config=sdk) for name in ("ec2", "iam", "ecr", "ssm"))
    tags = services["tags"]
    tag_list = [{"Key": key, "Value": value} for key, value in tags.items()]
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "is-default", "Values": ["true"]}])["Vpcs"]
    if len(vpcs) != 1:
        parser.error("One existing default VPC is required; no NAT/VPC will be created")
    vpc_id = vpcs[0]["VpcId"]
    subnets = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["Subnets"]
    subnet = next(
        s
        for s in sorted(subnets, key=lambda item: item["AvailabilityZone"])
        if s["MapPublicIpOnLaunch"]
    )
    ami = ssm.get_parameter(
        Name="/aws/service/canonical/ubuntu/server/24.04/stable/current/arm64/hvm/ebs-gp3/ami-id"
    )["Parameter"]["Value"]
    state = json.loads(args.state.read_text()) if args.state.exists() else {}
    if state and (state.get("account_id"), state.get("project"), state.get("region")) != (
        account,
        project,
        region,
    ):
        parser.error("Existing state belongs to a different deployment")
    state.update(
        account_id=account,
        project=project,
        region=region,
        vpc_id=vpc_id,
        subnet_id=subnet["SubnetId"],
        ami_id=ami,
        instance_type="t4g.small",
        estimated_daily_fixed_usd=0.5552,
        estimate_excludes="S3, ECR, AI, traffic, tax; no free tier assumed",
    )
    write_manifest(args.state, state)
    if not args.apply:
        print(json.dumps(state, indent=2))
        return

    role_name = project + "-runtime"
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "ec2.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    try:
        role = iam.get_role(RoleName=role_name)["Role"]
        owned({tag["Key"]: tag["Value"] for tag in role.get("Tags", [])}, project, role_name)
    except iam.exceptions.NoSuchEntityException:
        iam.create_role(
            RoleName=role_name, AssumeRolePolicyDocument=json.dumps(trust), Tags=tag_list
        )
    iam.attach_role_policy(
        RoleName=role_name, PolicyArn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
    )
    repository_arn = f"arn:aws:ecr:{region}:{account}:repository/{project}"
    resources = [
        f"arn:aws:bedrock:{r}::foundation-model/amazon.nova-lite-v1:0"
        for r in ("us-east-1", "us-east-2", "us-west-2")
    ]
    resources.append(
        f"arn:aws:bedrock:{region}:{account}:inference-profile/us.amazon.nova-lite-v1:0"
    )
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                "Resource": resources,
            },
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                "Resource": [
                    f"arn:aws:s3:::{bucket}/attachments/*",
                    f"arn:aws:s3:::{bucket}/agent-sessions/*",
                ],
            },
            {
                "Effect": "Allow",
                "Action": "s3:GetObject",
                "Resource": f"arn:aws:s3:::{bucket}/releases/*",
            },
            {
                "Effect": "Allow",
                "Action": "s3:PutObject",
                "Resource": f"arn:aws:s3:::{bucket}/backups/*",
            },
            {
                "Effect": "Allow",
                "Action": "s3:ListBucket",
                "Resource": f"arn:aws:s3:::{bucket}",
                "Condition": {"StringLike": {"s3:prefix": ["agent-sessions/*"]}},
            },
            {"Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*"},
            {
                "Effect": "Allow",
                "Action": [
                    "ecr:BatchGetImage",
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:BatchCheckLayerAvailability",
                ],
                "Resource": repository_arn,
            },
        ],
    }
    if mantle_statements:
        policy["Statement"][:1] = mantle_statements
    iam.put_role_policy(
        RoleName=role_name, PolicyName="OrderToWorkRuntime", PolicyDocument=json.dumps(policy)
    )
    try:
        profile = iam.get_instance_profile(InstanceProfileName=role_name)["InstanceProfile"]
    except iam.exceptions.NoSuchEntityException:
        profile = iam.create_instance_profile(InstanceProfileName=role_name, Tags=tag_list)[
            "InstanceProfile"
        ]
    owned({tag["Key"]: tag["Value"] for tag in profile.get("Tags", [])}, project, role_name)
    if not profile["Roles"]:
        iam.add_role_to_instance_profile(InstanceProfileName=role_name, RoleName=role_name)
    elif [role["RoleName"] for role in profile["Roles"]] != [role_name]:
        raise RuntimeError("Instance profile has an unexpected role")
    state["instance_profile"] = role_name
    write_manifest(args.state, state)

    try:
        repository = ecr.describe_repositories(repositoryNames=[project])["repositories"][0]
        existing_tags = ecr.list_tags_for_resource(resourceArn=repository["repositoryArn"])["tags"]
        owned({tag["Key"]: tag["Value"] for tag in existing_tags}, project, project)
    except ecr.exceptions.RepositoryNotFoundException:
        repository = ecr.create_repository(
            repositoryName=project,
            imageTagMutability="IMMUTABLE",
            imageScanningConfiguration={"scanOnPush": True},
            encryptionConfiguration={"encryptionType": "AES256"},
            tags=tag_list,
        )["repository"]
    ecr.put_lifecycle_policy(
        repositoryName=project,
        lifecyclePolicyText=json.dumps(
            {
                "rules": [
                    {
                        "rulePriority": 1,
                        "description": "Remove untagged build layers after one day",
                        "selection": {
                            "tagStatus": "untagged",
                            "countType": "sinceImagePushed",
                            "countUnit": "days",
                            "countNumber": 1,
                        },
                        "action": {"type": "expire"},
                    }
                ]
            }
        ),
    )
    state["repository_uri"] = repository["repositoryUri"]
    write_manifest(args.state, state)

    groups = ec2.describe_security_groups(
        Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "group-name", "Values": [project + "-web"]},
        ]
    )["SecurityGroups"]
    if groups:
        group = groups[0]
        owned(
            {tag["Key"]: tag["Value"] for tag in group.get("Tags", [])}, project, group["GroupId"]
        )
        group_id = group["GroupId"]
    else:
        group_id = ec2.create_security_group(
            GroupName=project + "-web",
            Description="OrderToWork HTTP HTTPS only; administration via SSM",
            VpcId=vpc_id,
            TagSpecifications=[{"ResourceType": "security-group", "Tags": tag_list}],
        )["GroupId"]
    for port in (80, 443):
        try:
            ec2.authorize_security_group_ingress(
                GroupId=group_id,
                IpPermissions=[
                    {
                        "IpProtocol": "tcp",
                        "FromPort": port,
                        "ToPort": port,
                        "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "Public web and ACME"}],
                    }
                ],
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "InvalidPermission.Duplicate":
                raise
    state["security_group_id"] = group_id
    write_manifest(args.state, state)

    instances = [
        instance
        for reservation in ec2.describe_instances(
            Filters=[
                {"Name": "tag:Project", "Values": [project]},
                {"Name": "tag:Name", "Values": [project + "-web"]},
                {
                    "Name": "instance-state-name",
                    "Values": ["pending", "running", "stopping", "stopped"],
                },
            ]
        )["Reservations"]
        for instance in reservation["Instances"]
    ]
    if len(instances) > 1:
        raise RuntimeError("Multiple project hosts found; refusing to create or choose another")
    if instances:
        instance = instances[0]
        owned(
            {tag["Key"]: tag["Value"] for tag in instance.get("Tags", [])},
            project,
            instance["InstanceId"],
        )
    else:
        instance = launch_instance(
            ec2,
            ImageId=ami,
            InstanceType="t4g.small",
            MinCount=1,
            MaxCount=1,
            ClientToken=project + "-single-host-v1",
            IamInstanceProfile={"Name": role_name},
            NetworkInterfaces=[
                {
                    "DeviceIndex": 0,
                    "SubnetId": subnet["SubnetId"],
                    "Groups": [group_id],
                    "AssociatePublicIpAddress": True,
                }
            ],
            MetadataOptions={
                "HttpTokens": "required",
                "HttpPutResponseHopLimit": 2,
                "HttpEndpoint": "enabled",
            },
            CreditSpecification={"CpuCredits": "standard"},
            BlockDeviceMappings=[
                {
                    "DeviceName": "/dev/sda1",
                    "Ebs": {
                        "VolumeSize": 12,
                        "VolumeType": "gp3",
                        "Encrypted": True,
                        "DeleteOnTermination": False,
                    },
                }
            ],
            TagSpecifications=[
                {
                    "ResourceType": "instance",
                    "Tags": tag_list + [{"Key": "Name", "Value": project + "-web"}],
                },
                {"ResourceType": "volume", "Tags": tag_list},
            ],
            UserData="#!/bin/bash\nsystemctl enable --now snap.amazon-ssm-agent.amazon-ssm-agent.service || systemctl enable --now amazon-ssm-agent.service\n",
        )["Instances"][0]
    state["instance_id"] = instance["InstanceId"]
    write_manifest(args.state, state)
    if instance["State"]["Name"] == "stopped":
        print("Existing host is stopped. Start it explicitly when ready to resume billing.")
        return
    addresses = ec2.describe_addresses(Filters=[{"Name": "tag:Project", "Values": [project]}])[
        "Addresses"
    ]
    if len(addresses) > 1:
        raise RuntimeError("Multiple project public IPs found; refusing another allocation")
    if addresses:
        address = addresses[0]
        owned(
            {tag["Key"]: tag["Value"] for tag in address.get("Tags", [])},
            project,
            address["AllocationId"],
        )
        if address.get("InstanceId") not in (None, instance["InstanceId"]):
            raise RuntimeError("Project public IP is associated with a different instance")
    else:
        address = ec2.allocate_address(
            Domain="vpc", TagSpecifications=[{"ResourceType": "elastic-ip", "Tags": tag_list}]
        )
    state.update(
        allocation_id=address["AllocationId"],
        public_ip=address["PublicIp"],
        domain=address["PublicIp"].replace(".", "-") + ".sslip.io",
    )
    write_manifest(args.state, state)
    ec2.get_waiter("instance_running").wait(
        InstanceIds=[instance["InstanceId"]], WaiterConfig={"Delay": 2, "MaxAttempts": 20}
    )
    ec2.associate_address(
        InstanceId=instance["InstanceId"],
        AllocationId=address["AllocationId"],
        AllowReassociation=False,
    )
    print(json.dumps(state, indent=2))


if __name__ == "__main__":
    main()
