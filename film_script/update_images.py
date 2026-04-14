# -*- coding=utf-8
from qcloud_cos import CosConfig
from qcloud_cos import CosS3Client
import requests
import sys
import logging

logging.basicConfig(level=logging.INFO, stream=sys.stdout)


region = 'ap-guangzhou'
config = CosConfig(Region=region, SecretId=secret_id, SecretKey=secret_key)
client = CosS3Client(config)

# 目标 URL
url = "https://sh1.pcie.pppoe.top/static/pics/p1768929827004296558.jpg"

try:
    # 1. 使用 requests 获取图片，直接读取为 content
    res = requests.get(url, timeout=10)
    res.raise_for_status() # 检查 HTTP 请求是否成功 (如 404, 500 等会抛出异常)

    # 2. 调用 put_object 进行上传
    response = client.put_object(
        Bucket='se-1338805106',
        Body=res.content,       # 【关键修改】：使用 res.content 直接传递二进制字节数据
        Key="images/test.jpg",  # 存储在 COS 上的路径
        ContentType=res.headers.get('Content-Type', 'image/jpeg') # 自动获取原图的 MIME 类型
    )
    print(f"上传成功，ETag: {response['ETag']}")

except Exception as e:
    print(f"发生错误: {e}")