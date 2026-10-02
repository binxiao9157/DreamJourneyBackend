# 本轮唯一最终指纹入口

日期：2026-09-29。旧run-03的final-03/04/07指纹是历史版本，不适用于本轮。

- iOS HEAD：`11d0d0051b9be3cce57822dd059472d1e2536866`（dirty）。
- Backend HEAD：`8141ff271228b78c35237f9947f4bf026332af43`（dirty，与进入本轮相同）。
- 四场受保护源码：`208a211361e14aed4d55ae1a7d375a9965bab46fe344a0048a0873ce427bc034`，依赖1297项。
- 四场构建manifest摘要：`f24adfcb8d0af1bd41bfb7df25e5f9475723bb708cdd2d9939317f0735f6c41b`，制品312项。
- logical20配置：`81db38e6a8f25c49ef136352baa18db749d78197e74335b5644682ebd320248f`。
- logical65配置：`a1804da3a39d1cf9e14e50e2e1ef9fc50e1add72f589cdfd517677a2260aab34`。
- generic iOS unsigned dylib：`f86107e3171d8e11fe36b7376c90b5617dfdc6176c40acdb3b2d2834d3a89761`。

同源不同编译配置需分开：native-mic-final-02启用受控SDK且只在DEBUG Simulator；all-ios-final-02为标准Simulator；generic-ios是实际设备目标编译、没有shim；四场runner标准Simulator默认App生命周期、未设隔离单元host环境。它们不是同一个二进制，不混用构建指纹。

本轮实际依赖有变化，未复用旧四场。最终冻结前补充取消入口直接断言发现产品清理缺陷，取得同断言红绿；没有沿用修前第一版四场。最后测试源码冻结后依次完成原生受控专项、标准全套、通用构建及四场；之后仅写文档/核验脚本，未改产品或测试依赖。最终重新计算源码与所有四场制品，均相符。

[逐文件依赖](final-protected-dependencies.json) · [构建manifest](four-scene-final-02/artifacts/source-build-manifest.json) · [最终重算](final-verification.json) · [核验脚本](verify_final.py) · [本轮修改文件](takeover-changed-files.json)。

构建初次失败与重建通过均有日志；不通过删除失败、降低测试断言或提高请求预算获得PASS。精确构建工具内部首因未证明，详见reproduction.md。
