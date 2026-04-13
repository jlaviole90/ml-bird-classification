"""AWS Lambda handler — batch inference triggered by S3 frame uploads.

When a new .jpg lands in the raw-frames bucket under the frames/ prefix,
this function downloads it, calls the SageMaker endpoint for bird species
classification, and writes the result back as a JSON sidecar in S3.
"""

from __future__ import annotations

import json
import os
import urllib.parse

import boto3

SAGEMAKER_ENDPOINT = os.environ["SAGEMAKER_ENDPOINT"]
S3_BUCKET = os.environ["S3_BUCKET"]

s3 = boto3.client("s3")
sagemaker_runtime = boto3.client("sagemaker-runtime")


def lambda_handler(event, context):
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])

        if not key.endswith(".jpg"):
            continue

        response = s3.get_object(Bucket=bucket, Key=key)
        image_bytes = response["Body"].read()

        sm_response = sagemaker_runtime.invoke_endpoint(
            EndpointName=SAGEMAKER_ENDPOINT,
            ContentType="application/x-image",
            Body=image_bytes,
        )
        prediction = json.loads(sm_response["Body"].read().decode())

        result_key = key.replace(".jpg", "_prediction.json")
        s3.put_object(
            Bucket=bucket,
            Key=result_key,
            Body=json.dumps(prediction, indent=2).encode(),
            ContentType="application/json",
        )

    return {"statusCode": 200, "body": f"Processed {len(event.get('Records', []))} frames"}
