# 航空气象分析

## 任务方法

基于 NOAA Aviation Weather 公开接口获取指定机场的实时气象与预报：

1. 从输入取得 ICAO 机场代码（如 ZBAA）。
2. 依次调用 `aviation.noaa.get_metar`（实况 METAR）与 `aviation.noaa.get_taf`
   （ terminal 预报 TAF）。
3. 返回结构化报告列表与带来源 URI/获取时间的证据条目；未取得数据时如实
   返回空结果，不编造气象信息。

## 边界

- 只读公开接口；仅外发查询参数（机场代码），绝不外发本地资料内容。
- 演练模式下只做预检（dry_run），不发起真实网络请求。
