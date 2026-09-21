# 机场要素检索

## 任务方法

在 OurAirports 离线快照中检索机场要素：

1. 接收 ICAO/IATA 代码或机场/城市名称。
2. 调用 `aviation.ourairports.lookup_airport` 做精确代码匹配与名称模糊匹配。
3. 返回机场列表与注明数据快照日期的证据条目。

## 边界

- 完全离线（本地 CSV 快照），drill 演练模式下也真实执行。
- 快照数据有日期，结果不代表现时航行资料；正式运行前需人工核对有效 AIP/NOTAM。
