# CAP / KEEP 执行清单

## D1-D4

- [x] D1 生产共享 raw event dispatcher，QueryConfirmed 与 voice 隔离，Chat/TTS 隔离。
- [x] D2 observation、seal、immutable delivery、typed issue 分离并持久化。
- [x] D3 Controller/Coordinator 所有权、stop watermark、逐 observation/boundary 排空。
- [x] D4 typed 磁盘错误、128/4MiB 有界恢复、固定首错和脱敏诊断。

## CAP

- [x] CAP-01 PASS：同文、句号容差、反例、封存前修订、封存后不可变。
- [x] CAP-02 PASS：A→B→ASREnded 仅一个 B delivery。
- [x] CAP-03 PASS：QueryConfirmed 不覆盖 voice，合法 text ack 正常。
- [x] CAP-04 PASS：无 boundary、仅 interim、boundary 先到、跨 question 迟到。
- [x] CAP-05 PASS：Chat/TTS 顺序、重复与跨 reply 隔离。
- [x] CAP-06 PASS：Owner/assistant 冲突持久化，后续 10 回合和 close intent 保留。
- [x] CAP-07 PASS：六个写阶段单次失败原子恢复。
- [x] CAP-08 PASS：持续失败、损坏、溢出、恢复不假报完整。
- [x] CAP-09 PASS：stop 逐包屏障、close intent 后重建。
- [x] CAP-10 PASS：离页、Controller 重建、冷启动恢复。
- [x] CAP-11 PASS：只读排空确认后下一轮继续。
- [x] CAP-12 PASS：多 TTL、断网、401、deny、账号切换、迟到回调。
- [x] CAP-13 PASS：首错经 10,000 高频事件及重建不覆盖。
- [x] CAP-14 PASS：13→33、逻辑 20m/100、65m/150。
- [x] CAP-15 PASS：客户端→Backend→PG→候选→审核→正式记忆→重建读取。

## KEEP

- [x] KEEP-01 短场保存和补充。
- [x] KEEP-02 长场持续采集和分批整理。
- [x] KEEP-03 重复、补充、纠正、撤回。
- [x] KEEP-04 B7 纯问题与用户事实证据。
- [x] KEEP-05 候选审核、Binding/CAS、正式记忆和重建。
- [x] KEEP-06 长回答、主动打断、恢复聆听、PCM、租约。
- [x] KEEP-07 strict final、非ASR过滤、partial。
- [x] KEEP-08 TTL、认证、账号、旧 poll。
- [x] KEEP-09 未知写、失败 Run、一次额外预算。
- [x] KEEP-10 当前场状态与恢复坐标。

## 非本地项

- [ ] REAL_PROVIDER：NOT_RUN。
- [ ] REAL_SDK_DEVICE_ORDER：NOT_RUN。
- [ ] DEVICE_SHORT：NOT_RUN。
- [ ] DEVICE_20M：NOT_RUN。
- [ ] DEVICE_65M：NOT_RUN。
- [ ] DEPLOY：NOT_RUN。
- [ ] HISTORICAL_REPROCESS：NOT_RUN。
