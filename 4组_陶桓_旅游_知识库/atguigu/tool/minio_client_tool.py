import json

from minio import Minio

from atguigu.config.config import MinIoConfig
from atguigu.tool.logger import logger

minio_client=None

def get_minio_client():
    global minio_client
    if not minio_client:
        try:
            minio_client = Minio(
                endpoint=MinIoConfig.minio_endpoint,
                access_key=MinIoConfig.minio_access_key,
                secret_key=MinIoConfig.minio_secret_key,
                secure=False
            )
            bucket_name=MinIoConfig.minio_bucket_name
            #判断桶存在不存在,不存在就创建
            if not minio_client.bucket_exists(bucket_name = bucket_name):
                minio_client.make_bucket(bucket_name = bucket_name)
                logger.info(f"创建桶{bucket_name}成功")
            #设置权限
            policy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"AWS": "*"},
                        "Action": ["s3:GetBucketLocation", "s3:ListBucket"],
                        "Resource": f"arn:aws:s3:::{bucket_name}",
                    },
                    {
                        "Effect": "Allow",
                        "Principal": {"AWS": "*"},
                        "Action": "s3:GetObject",
                        "Resource": f"arn:aws:s3:::{bucket_name}/*",
                    },
                ],
            }
            minio_client.set_bucket_policy(bucket_name=bucket_name, policy=json.dumps(policy))
        except Exception as e:
            logger.error(f"创建桶{bucket_name}失败:{e}")
            raise Exception(f"创建桶{bucket_name}失败")
    return minio_client