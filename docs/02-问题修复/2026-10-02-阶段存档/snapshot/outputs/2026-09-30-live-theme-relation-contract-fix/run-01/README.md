# 2026-09-30 关系模型输出合同局部修复

当前：局部代码与63项相关回归PASS；同一真实失败材料修后关系及独立复核PASS。用户已明确允许继续；补充/更正4次真实检查均PASS。正在发布新后端并核验Worker，尚未开始手机场次，不标全链路修复完成。

## 修改与保留

仅一个后端运行文件变化：`app/services/owner_truth_live_theme_provider.py`。明确无更正时replaces必须为空对象{}；纠正时为新ID到旧ID的非空对象；duplicateAtomIds为数组。补全none、uncertain、supplement、duplicate、correction五种输出示例，非空标题/摘要限制和target版本/hash来源明确。纯重复只重述双方共同支持的事实，不能混入仅旧证据支持的其他内容。

保持校验器、Worker、保存链、预算、超时、最大4096输出token及48000请求字节上限不变。不对模型错误数组做静默类型转换，不扩大重试。

- [变更差异](provider-change.patch)
- [源码指纹](source-fingerprints.json)
- [完整运行包指纹](runtime-manifest.json)
- [新增测试](test_relation_contract.py)，已复制到后端tests/test_live_relation_output_contract.py。

## 验证

- [本地合同红](local-red.log)：旧代码缺完整关系示例，4项既有严格断言通过、1项新合同检查报缺少方法。此红只证明新增结构合同，不称为独立业务红。
- [本地绿和相关回归](local-regression.log)：63项通过，包含实际空数组失败继续拒绝、空摘要拒绝、错目标/错误去重拒绝、必须独立复核等。
- [真实修前证据](../../2026-09-30-live-model-contract-device/run-01/provider-production-shape.log)：生产形状合成材料返回replaces=[]，验证themeRelationMemberMismatch。
- [真实修后证据](prepare-and-provider.log)：同一material逐字段完全一致，返回replaces={}，关系和独立支持复核均PASS。2次请求，无自动重试。
- [同材料对照](same-material-comparison.json)：不把单个成功样本推广为所有模型场景均稳定；旧失败保留。

此前真实请求正常返回、finish_reason=stop，无容量/超时证据。此修复解决已复现的合同歧义，不声称证明历史全部长场失败原因，也不关闭历史音频间隙/seq5。

## 发布与真机边界

新后端release live-contract-recovery-v2-20260930及镜像已准备，schema0128核验通过；当前线上仍live-resilient-20260930，未激活新release。iOS源码和前轮签名包无变化，后端最终指纹已记录。原旧版API回滚镜像保留。

计划先检查补充/更正真实输出，通过后部署并核对全部Worker，安装签名包，先短场再物理20分钟，再独立短场和物理40分钟。真实长场不得复用旧版短场凭据。

额外4次关系请求被自动审批拒绝，理由是累计请求超出历史8次诊断额度；被拒绝命令未执行。已向用户明确询问后续真实调用与真机流程授权。此为执行授权限制，不算产品测试失败。未commit/push，未改历史业务数据。

## 授权后续进展

- 用户明确“允许”额外真实验证、部署及短→20→短→40流程，取代此前审批额度阻断。
- 首次附加探针在发请求前因合成evidenceId计算方式错误而停止（0次调用），原装配错误保存provider-extra-fixture-error.log；按生产原文范围哈希算法修正后，补充/更正各关系与独立复核共4次PASS。
- [附加真实证据](provider-extra.log)、[汇总](provider-summary.json)。这次是真实模型调用，未伪造provider返回。
- [发布过程](deploy-verify.log)持续记录；手机测试沿用上轮已构建且重新核对的STAGE01，待全服务版本核对完成后启动。

## 当前最终进展

发布已完成并核验7容器同版；实际iPhone短场两轮/4正文完整，但发现独立主题none语义误拦截，候选0。该规则在本轮格式修复前后均未改动。见[真机直接原因报告](../../2026-09-30-live-model-contract-device/run-01/真机短场失败与直接原因.md)。短场FAIL，物理20/40未运行，不把格式专项PASS当整条记忆链PASS。
