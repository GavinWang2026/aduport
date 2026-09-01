# 企业一机一码 License

## 客户机

```bash
mdbridge machine-id
# 或 GET http://127.0.0.1:8000/api/license → machine_id
```

把 `machine_id` 发给实施方。

## 实施方签发

```bash
export MDBRIDGE_LICENSE_SECRET='your-shared-secret'
mdbridge license-issue -m '<customer-machine-id>' -e 2027-12-31 -c 'Acme' -s 1
```

## 客户部署

```bash
# enterprise/.env
MDBRIDGE_LICENSE_REQUIRED=true
MDBRIDGE_LICENSE_SECRET=your-shared-secret   # 须与签发一致
MDBRIDGE_LICENSE_KEY='<issued-token>'
# 或 MDBRIDGE_LICENSE_FILE=/data/license.key
```

校验：`mdbridge license-check` 或打开 `/api/license`。

**说明：** HMAC 绑定机器指纹 + 到期日，防随意拷贝部署；非军工级防破解。开源自托管默认 `LICENSE_REQUIRED=false`。
