# CRYPTO ALPHA 会员 UID 验证

用户打开验证页，填写 Gate、OKX 或 Binance 的 UID，以及 QQ / Telegram。服务用我们自己的推广接口核对这个 UID 是不是推广名下的用户。用户不需要提供交易所密码或 API Key。

## 三个交易所的接口

| 交易所 | 能否按 UID 核对 | 使用的接口 |
| --- | --- | --- |
| Gate | 可以 | `GET /api/v4/rebate/partner/sub_list`，再用 `GET /api/v4/rebate/user/sub_relation` 辅助 |
| OKX | 可以 | `GET /api/v5/affiliate/invitee/detail`，查不到时再用 `GET /api/v5/affiliate/invitee/list` |
| Binance | 可以 | `GET /sapi/v1/affiliate/performance/invitee` |

币安核对时不传 `startDate` / `endDate`，接口返回的是这个 UID 的全时段身份。没交易量也算推广用户。Key 没配时，才退回 `BINANCE_UID_FILE` 名单。

三家 Key 都只开返佣 / Affiliate 的只读权限，不要开交易和提现，并加上服务器 IP 白名单。

## 部署

```bash
cp .env.example .env
# 填入 Gate、OKX、Binance 的只读 Key，以及 ADMIN_TOKEN
docker compose up -d --build
```

页面在 `http://服务器:8080/`。

查看申请：

```bash
curl -H "Authorization: Bearer $ADMIN_TOKEN" http://127.0.0.1:8080/api/applications
```

币安名单每行一个 UID，也兼容 CSV 的第一列：

```text
# binance_uids.txt
12345678
23456789,2026-01-01
```

`MOCK_VERIFY=1` 只给本机演示，正式环境必须关掉。
