# 2026-09-30 真机测试入口与分阶段记录

最新状态：DEPLOY_PASS / DEVICE_SHORT_FAIL / DEVICE_20M_NOT_RUN / DEVICE_40M_NOT_RUN。

[真机短场失败与直接原因](真机短场失败与直接原因.md)：格式修复后已发布安装；短场4/4正文完整，但独立新主题被内部合并条件误拦截。以下为首次预检时的历史记录，保留、不覆盖。

## 任务与实际边界

用户已连接手机并授权真机测试。设备已识别；按既定流程检查同版代码、构建签名包、校验真实模型请求，然后才安装、短场及物理20/40分钟。静音不是门禁。短场须在每次长场前独立通过。

当前1251文件与上轮最终源码指纹一致，见[source-preflight.json](source-preflight.json)。[签名构建](build.log)成功，STAGE01保留且未安装。新后端镜像在隔离one-off容器完成schema0128只读核验，未新增迁移。新release目录与镜像已准备，API和Worker未切换；原release仍是live-resilient-20260930，[最终服务检查](server-final-state.txt)ready。保留原API镜像回滚tag pre-contract-recovery-20260930。未创建业务任务，未修改生产业务或历史数据，未commit/push。

## 请求及证据

实际域名api.deepseek.com，模型deepseek-v4-flash。从新镜像调用生产DeepSeekLiveThemeProvider的prepare/request_prepared/validate方法；凭据只在服务容器内使用。所有输入都是新造合成事实，没有读取用户原对话。

共6次模型请求：原计划5次包含前三个阶段、关系和独立关系复核；第4次关系校验失败，未执行依赖的独立复核。第5次保存关系失败原始合成返回；发现探针缺少生产材料中的theme字段后，新增一次完整材料对照，第6次仍失败，未再调用独立复核。没有自动重试，也没有用后来结果抹掉前次失败。

| 次数 | 项目 | 结果 |
|---|---|---|
| 1 | themeOrganization | 返回及严格校验PASS |
| 2 | themeSupport | 返回及严格校验PASS，确有1个supported主题 |
| 3 | themeSafety | 返回及严格校验PASS，safe |
| 4 | themeRelation，初始材料 | themeRelationMemberMismatch；未保留此次原始payload，不能臆断具体字段 |
| 5 | 同类材料，保留合成payload | duplicateAtomIds正确；replaces=[]、空title/summary，严格校验FAIL |
| 6 | 补齐当前主题及旧主题字段的生产形状材料 | duplicateAtomIds正确；非空title/summary；仍replaces=[]，严格校验FAIL |
| — | themeRelationSupport | 上游关系不合法，NOT_RUN |

原始证据：[前4次](provider-smoke.log)、[第5次合成原文](provider-relation-diagnostic.log)、[第6次完整材料合成原文](provider-production-shape.log)。脚本同目录保留。第5、6次材料仅1条新事实、1条旧事实，身份synthetic-new/old，均为“我去年在杭州读书”。共享原文证据是这个合成场景的特点，不代表全部真实跨场输入分布。

## 目前能确定什么

生产校验器app/domain/owner_truth/live_theme_relations.py要求replaces为dict；没有更正关系也必须是空对象{}。实际模型两次返回空数组[]，所以进入themeRelationMemberMismatch。第6次没有空标题问题，说明初始探针缺theme确实影响输入完整性，但不能解释补齐后仍存在的类型错误。

这些响应finish_reason均为stop、正常解码；第6次prompt_tokens1234、completion_tokens154。没有HTTP拒绝、截断或超时证据。当前可以确定的是这组小合成输入上，真实模型输出与产品结构合同不一致；不能据此推断所有真实长场历史失败都由此导致，或认定火山Live、麦克风有故障。

提示词描述了“replaces(新atomId到明确被纠正的旧atomId)”，但关系示例仅示范none，没有直接展示非更正时空对象格式；这可能增加歧义，仍是待验证的改进方向，不是经对照证明的唯一根因。严格校验拒绝本身是正确的保护，本轮没有将[]强行转换成{}或放宽校验。

## 为什么未启动手机

实际模型前置检查已经失败，不能将其标通过再安排20/40分钟。此次不是新真机FAIL，而是DEVICE_NOT_RUN；没有新Live可验证异常收尾。保留此前已有异常后产品恢复流程，未临时修改工具或业务代码。用户此前要求先记录分析、讨论再修改，本轮按此边界留证。

## 下一步

讨论后修正生产关系输出合同的明确性，并补真实供应商结构对照，保留错误返回反例及严格引用/身份校验。真实合同通过后重新核对源码和构建一致性、部署并验证所有Worker，再按短A→物理20→短B→物理40执行。历史seq5、0.542秒音频间隙原因仍未决；C01持续原生音频与保存同线证据仍待真机。
