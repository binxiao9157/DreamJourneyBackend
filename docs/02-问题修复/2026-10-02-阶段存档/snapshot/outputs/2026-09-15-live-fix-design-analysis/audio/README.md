# 音频机制受控探针

当前证据：11项生产源片段/真实纯reducer的机制观察。SDK/engine/delegate为spy，不是完整App、真实SDK音频或历史现场复现；修复绿、蓝牙真机均NOT_RUN。源SHA256与片段清单见source-manifest.json。

先运行`python3 validate-source-probe.py`核对源码没有变化。源码变化后应重新逐字抽取并审查，不能继续沿用旧证据。此脚本核对片段字节与源码指纹，不判断业务正确。

在本目录运行`xcrun swiftc -module-cache-path /private/tmp/live-audio-source-probe-cache audio-source-probe.swift -o audio-source-probe`，然后`./audio-source-probe source-probe-results.json`。只编译macOS Swift源码，不启动模拟器/手机，不联网。

probe所用文本及question/reply ID均为合成测试值。不要将真实私人历史填入此fixture。probe没有实施拟议修复，因此MECHANISM_OBSERVED不是修复PASS。
