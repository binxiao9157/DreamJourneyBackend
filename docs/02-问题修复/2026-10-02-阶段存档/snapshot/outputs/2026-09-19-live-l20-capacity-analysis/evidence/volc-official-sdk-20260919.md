<span id="def01b11"></span>
# 集成指南
<span id="a6a1e41f"></span>
## SDK 简介
Dialog语音对话 SDK 是基于豆包端到端实时语音大模型，使用设备自身的录音机与播放器，提供低延迟、双向流式语音交互能力，可用于构建语音到语音的对话工具。
<span id="3376cb8d"></span>
## SDK版本
Android: com.bytedance.speechengine:speechengine_tob:0.0.14.7
iOS: pod 'SpeechEngineToB', '0.0.14.7'
注：0.0.14版本开始SDK不再依赖TTNet，但Android依赖okhttp，iOS 依赖 SocketRocket。变更后依赖示例如下：
Android:
0.0.14及之后版本
```Java
// Speech Engine
implementation 'com.bytedance.speechengine:speechengine_tob:0.0.14.6.1-bugfix'
// Net  okhttp:4.9.1为默认依赖的网络库版本，若有其它版本需要，可引用相应okhttp版本
implementation("com.squareup.okhttp3:okhttp:4.9.1")
```

0.0.14之前版本
```C++
implementation 'com.bytedance.boringssl.so:boringssl-so:1.3.7-16kb'
implementation('org.chromium.net:cronet:4.2.210.4-tob') {
    exclude group: 'com.bytedance.common', module: 'wschannel'
}
implementation 'com.bytedance.frameworks.baselib:ttnet:4.2.210.4-tob'
```

iOS:
0.0.14及之后版本
```Ruby
# Net  0.6.1为默认依赖的网络库版本，若有其它版本需要，可引用相应SocketRocket版本
pod 'SocketRocket', '0.6.1'
```

0.0.14之前版本
```C++
pod 'TTNetworkManager', '4.2.210.20'
```

<span id="e20213b8"></span>
## 组件依赖
```Plain Text
source 'https://github.com/CocoaPods/Specs.git'
source 'https://github.com/volcengine/volcengine-specs.git'

# TTNet
pod 'TTNetworkManager', '{LATEST_VERSION}'

# Speech
pod 'SpeechEngineToB', '{LATEST_VERSION}'
```

<span id="7929d442"></span>
## 兼容性

| | | \
|**类别** |**兼容范围** |
|---|---|
| | | \
|系统 |最低支持 iOS 12.0 |
| | | \
|架构 |armv7，arm64，x86_64 |
| | | \
|网络 |支持移动数据与 WiFi 两种网络环境 |

<span id="a59af579"></span>
## 回声消除 AEC
Dialog 语音对话过程中，如果既启用录音机，又启用播放器，则设备会录入播放的人声而影响对话。故Dialog SDK 内置 AEC能力，对回声进行消除处理。但需要开启AEC功能，并配置AEC模型文件路径。
特别的，在自定义输入音频或自定义输出音频场景，如果能确保输入音频没有回声，可以选择关闭AEC。如果不能，则需要自行保证输出音频和输入回声所处的时间位置一致，以便AEC进行处理。
（当前硬件AEC为强制开启，故可能存在可能关闭Dialog SDK内置AEC之后，硬件AEC继续生效不会自打断。主要原因是为了设备当前有其它APP存在播放的声音时，该声音Dialog SDK内置AEC消除不掉，故强制开启硬件AEC 作为保底。）
AEC 模型版本如下：
<Attachment link="https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/155506a530974d7186d8a3c49ed495b8~tplv-goo7wpa0wc-image.image" name="aec.model" ></Attachment>
<span id="03c70f37"></span>
# 调用流程
<span id="c0e4d2d9"></span>
## 初始化环境依赖
创建语音对话 SDK 引擎实例前调用，完成网络环境等相关依赖配置。APP 生命周期内仅需执行一次。
```objectivec
- (BOOL)application:(UIApplication *)application didFinishLaunchingWithOptions:(NSDictionary *)launchOptions {
    return [SpeechEngine prepareEnvironment];
}
```

<span id="d584b4db"></span>
## 创建引擎实例
语音对话 SDK 通过如下方式获取相关实例。
```objectivec
//创建实例
self.engine = [[SpeechEngine alloc] init];
//添加引擎代理，需要实现回调方法
[self.engine createEngineWithDelegate:self];
```

<span id="0538d2bf"></span>
## 参数配置
其中 APPID 、TOKEN 获取方式参考 [控制台使用FAQ-Q1](https://www.volcengine.com/docs/6561/196768#q1%EF%BC%9A%E5%93%AA%E9%87%8C%E5%8F%AF%E4%BB%A5%E8%8E%B7%E5%8F%96%E5%88%B0%E4%BB%A5%E4%B8%8B%E5%8F%82%E6%95%B0appid%EF%BC%8Ccluster%EF%BC%8Ctoken%EF%BC%8Cauthorization-type%EF%BC%8Csecret-key-%EF%BC%9F) ，RESOURCE ID 设置为 `volc.speech.dialog`。
```objectivec
//【必需配置】Engine Name
[self.engine setStringParam:SE_DIALOG_ENGINE forKey:SE_PARAMS_KEY_ENGINE_NAME_STRING];

//【可选配置】本地日志文件路径，SDK会在该路径文件夹下生成名为 speech_sdk.log 的日志文件，开发时设置，线上关闭。
[self.engine setStringParam:@"已存在的文件夹路径，或者空字符串" forKey:SE_PARAMS_KEY_DEBUG_PATH_STRING];
//【可选配置】日志级别，开发时设置为 TRACE（最低级别），线上设置 WARN；
[self.engine setStringParam:SE_LOG_LEVEL_TRACE forKey:SE_PARAMS_KEY_LOG_LEVEL_STRING];

//【必需配置】Authentication：Appid
[self.engine setStringParam:@"APPID" forKey:SE_PARAMS_KEY_APP_ID_STRING];
//【必需配置】Authentication：AppKey
[self.engine setStringParam:@"APPKEY" forKey:SE_PARAMS_KEY_APP_KEY_STRING];
//【必需配置】Authentication：Token
[self.engine setStringParam:@"ACCESS TOKEN" forKey:SE_PARAMS_KEY_APP_TOKEN_STRING];
//【必需配置】对话服务资源信息ResourceId
[self.engine setStringParam:@"RESOURCE ID" forKey:SE_PARAMS_KEY_RESOURCE_ID_STRING];
//【必需配置】User ID（用以辅助定位线上用户问题，如无法提供可提供固定字符串）
[self.engine setStringParam:@"UID" forKey:SE_PARAMS_KEY_UID_STRING];
//【必需配置】对话服务域名
[self.engine setStringParam:@"wss://openspeech.bytedance.com" forKey:SE_PARAMS_KEY_DIALOG_ADDRESS_STRING];
//【必需配置】对话服务Uri
[self.engine setStringParam:@"/api/v3/realtime/dialogue" forKey:SE_PARAMS_KEY_DIALOG_URI_STRING];

// AEC
//【可选配置】是否开启AEC，默认不开启，同时启用设备录音和播放时必须开启
[self.engine setBoolParam:TRUE forKey:SE_PARAMS_KEY_ENABLE_AEC_BOOL];
//【可选配置】AEC模型路径，开启AEC时必填
[self.engine setStringParam:@"AEC 模型文件路径，路径需包含文件名，示例：/xxx/xxx/aec.model" forKey:SE_PARAMS_KEY_AEC_MODEL_PATH_STRING];

// 输入音频 & 录音机
//【可选配置】配置音频来源，默认使用设备麦克风录音（Dialog 仅支持 RECORDER 和 STREAM 模式，RECORDER表示设备麦克风录音，STREAM表示自定义音频输入）
[self.engine setStringParam:SE_RECORDER_TYPE_RECORDER forKey:SE_PARAMS_KEY_RECORDER_TYPE_STRING];
//【可选配置】录音文件保存路径，如不为空，则SDK会将录音机音频保存到该路径下，文件格式为 .wav
[self.engine setStringParam:@"已存在的文件夹路径，或者空字符串" forKey:SE_PARAMS_KEY_DIALOG_RECORDER_PATH_STRING];
//【可选配置】启用录音机音频回调，开启后录音音频数据将通过SEDialogRecorderAudio消息回调，默认不启用
[self.engine setBoolParam:FALSE forKey:SE_PARAMS_KEY_DIALOG_ENABLE_RECORDER_AUDIO_CALLBACK_BOOL];

// 输出音频 & 播放器
//【可选配置】是否开启播放器，默认开启
[self.engine setBoolParam:TRUE forKey:SE_PARAMS_KEY_DIALOG_ENABLE_PLAYER_BOOL];
//【可选配置】启用播放器音频回调，默认不启用（为当前正在播放的音频数据，会随着播放进度回调）
[self.engine setBoolParam:FALSE forKey:SE_PARAMS_KEY_DIALOG_ENABLE_PLAYER_AUDIO_CALLBACK_BOOL];
//【可选配置】启用解码后原始音频回调，默认不启用（为解码后的需要播报的数据，会在解码完成后立刻回调，不等待播放进度）
[self.engine setBoolParam:FALSE forKey:SE_PARAMS_KEY_DIALOG_ENABLE_DECODER_AUDIO_CALLBACK_BOOL];
//【可选配置】播放文件保存路径，如不为空字符串，则SDK会将播放器音频保存到该路径下，文件格式为 .wav
[self.engine setStringParam:@"已存在的文件夹路径，或者空字符串" forKey:SE_PARAMS_KEY_DIALOG_PLAYER_PATH_STRING];
```

<span id="e4580afa"></span>
## 初始化引擎实例
初始化引擎对象并设置回调监听。
```objectivec
// 初始化引擎实例
SEEngineErrorCode ret = [self.engine initEngine];
if (ret != SENoError) {
    NSLog(@"初始化失败，返回值: %d", ret);
    return;
}
```

<span id="08c01c93"></span>
## 发送指令
语音对话 SDK 通过发送指令接口 `sendDirective` 触发各种操作，需要注意不建议在 SDK 的回调线程中调用该接口。
<span id="cbd7210b"></span>
### 启动引擎
启动引擎的传参，请参考[端到端实时语音大模型API接入文档--豆包语音-火山引擎](https://www.volcengine.com/docs/6561/1594356) 中 客户端事件 -> StartSession 的参数说明。
```objectivec
// 注意这里先调用同步停止，避免SDK内部异步线程带来的问题
[self.engine sendDirective:SEDirectiveSyncStopEngine];
// 启动Dialog引擎
SEEngineErrorCode ret = [self.engine sendDirective:SEDirectiveStartEngine data:"{\"dialog\":{\"bot_name\":\"豆包\"}}"];
if (ret != SENoError) {
    NSLog(@"Send directive say hello failed: %d", ret);
    return;
}
```

<span id="8ed0f0de"></span>
### 播报开场白
播报开场白的传参，请参考[端到端实时语音大模型API接入文档--豆包语音-火山引擎](https://www.volcengine.com/docs/6561/1594356) 中 客户端事件 -> SayHello 的参数说明。
```objectivec
SEEngineErrorCode ret = [self.engine sendDirective:SEDirectiveEventSayHello data:@"{\"content\": \"我是你的AI助手，请问有什么可以帮你。\"}"];
if (ret != SENoError) {
    NSLog(@"播报开场白失败，返回值: ", ret);
    return;
}
```

<span id="a8a29043"></span>
### 自定义TTS ：自定义回复文本
仅开启`SEDialogWorkModeDelegateChatTtsText`模式时可用。
**自定义文本回复**：播报自定义的文本内容，会替换Dialog 助手自动回复的文本。
**自定义文本回复**的传参，请参考[端到端实时语音大模型API接入文档--豆包语音-火山引擎](https://www.volcengine.com/docs/6561/1594356) 中 客户端事件 -> ChatTTSText 的参数说明。
```objectivec
// 初始化前，设置参数开启 DIALOG_WORK_MODE_DELEGATE_CHAT_TTS_TEXT 模式
[self.engine setIntParam:SEDialogWorkModeDelegateChatTtsText forKey:SE_PARAMS_KEY_DIALOG_WORK_MODE_INT];

// ... 初始化引擎实例 ...

// ... 启动引擎 ...

// ... 接收到 SEDialogASREnded 事件。标志用户已经说完一句话，等待回复 ...

// Directive：发送ChatTtsText指令以播放自定义回复文本，可以流式不断补充文本内容。首包需要包含start:true，end:false 。
NSString* chatTtsTextJson = @"{\"start\": true, \"content\": \"这里是自定义回复内容\", \"end\": false}";
SEEngineErrorCode ret = [self.engine sendDirective:SEDirectiveDialogChatTtsText data:chatTtsTextJson];
if (ret != SENoError) {
    NSLog(@"自定义TTS回复失败: %d", ret);
    return;
}
// Directive：发送ChatTtsText指令以播放自定义回复文本，可以流式不断补充文本内容。尾包需要包含start:false，end:true 。
chatTtsTextJson = @"{\"start\": false, \"content\": \"\", \"end\": true}";
ret = [self.engine sendDirective:SEDirectiveDialogChatTtsText data:chatTtsTextJson];
if (ret != SENoError) {
    NSLog(@"自定义TTS回复失败: %d", ret);
    return;
}
```

<span id="f676c747"></span>
### 自定义TTS：播放**客户端指定的TTS回复**
仅开启`SEDialogWorkModeDelegateChatTtsText`模式时可用：开启`SEDialogWorkModeDelegateChatTtsText`模式后，每轮对话都需要选择使用 **客户端指定的TTS回复** 或 **服务自动生成的TTS回复**，否则将不会进行TTS播报。
**客户端指定的TTS回复**：回复内容都是通过主动调用 SDK发送指令 接口触发的，包含以下类型：

* SayHello 指令触发的开场白音频，对应 tts_type 为 chat_tts_text。
* ChatTtsText 指令触发的指定文本回复，对应 tts_type 为 chat_tts_text。
* ChatRagText 指令触发的外部RAG总结回复，对应 tts_type 为 external_rag。

```objectivec
// 初始化前，设置参数开启 DIALOG_WORK_MODE_DELEGATE_CHAT_TTS_TEXT 模式
[self.engine setIntParam:SEDialogWorkModeDelegateChatTtsText forKey:SE_PARAMS_KEY_DIALOG_WORK_MODE_INT];

// ... 初始化引擎实例 ...

// ... 启动引擎 ...

// ... 接收到 SEDialogASRInfo 事件。标志用户已经说完一句话，等待回复 ...

// Directive：发送UseClientTriggerTts指令，本轮对话将播放Dialog客户端指定的TTS回复。
SEEngineErrorCode ret = [self.engine sendDirective:SEDirectiveDialogUseClientTriggerTts];
if (ret != SENoError) {
    NSLog(@"播放客户端指定的TTS回复失败: %d", ret);
    return;
}
```

<span id="39eb1e8e"></span>
### 自定义TTS：播放服务自动生成的TTS回复
仅开启`SEDialogWorkModeDelegateChatTtsText`模式时可用：开启`SEDialogWorkModeDelegateChatTtsText`模式后，每轮对话都需要选择使用 **客户端指定的TTS回复** 或 **服务自动生成的TTS回复**，否则将不会进行TTS播报。
**服务自动生成的TTS回复**：不需要主动调用SDK接口，服务会自动生成下发，包含以下类型：

* 闲聊，即默认TTS回复，对应 tts_type 为 default。
* 安全审核音频，对应 tts_type 为 audit_content_risky。
* 内置联网音频，对应 tts_type 为 network。

```objectivec
// 初始化前，设置参数开启 DIALOG_WORK_MODE_DELEGATE_CHAT_TTS_TEXT 模式
[self.engine setIntParam:SEDialogWorkModeDelegateChatTtsText forKey:SE_PARAMS_KEY_DIALOG_WORK_MODE_INT];

// ... 初始化引擎实例 ...

// ... 启动引擎 ...

// ... 接收到 SEDialogASRInfo 事件。标志用户已经说完一句话，等待回复 ...

// Directive：发送UseServerTriggerTts指令，本轮对话将播放Dialog服务自动生成的TTS回复音频。
SEEngineErrorCode ret = [self.engine sendDirective:SEDirectiveDialogUseServerTriggerTts];
if (ret != SENoError) {
    NSLog(@"播放服务自动生成的TTS回复失败: %d", ret);
    return;
}
```

<span id="e0e8885a"></span>
### 发送文本Query
Dialog 支持不通过语音，而是通过输出文本的方式，触发语音回复。传参请参考 [端到端实时语音大模型API接入文档--豆包语音-火山引擎](https://www.volcengine.com/docs/6561/1594356) 中 客户端事件 -> ChatTextQuery 的参数说明。
```objectivec
SEEngineErrorCode ret = [self.engine sendDirective:SEDirectiveEventChatTextQuery data:@"{\"content\": \"1+1等于几\"}"];
if (ret != SENoError) {
    NSLog(@"文本Query失败，返回值: %d", ret);
    return;
}
```

<span id="25ab6f9d"></span>
### 发送 RAG Query
Dialog 支持输入外部RAG知识，通过模型的总结和口语化改写之后输出对应音频。传参和具体使用请参考 [端到端实时语音大模型API接入文档--豆包语音-火山引擎](https://www.volcengine.com/docs/6561/1594356) 中 客户端事件 -> ChatRAGText 的参数说明。
```objectivec
SEEngineErrorCode ret = [self.engine sendDirective:SEDirectiveEventChatRagText data:@"{\"external_rag\":\"[{\\\"title\\\":\\\"Dialog语音SDK\\\",\\\"content\\\":\\\"Dialog语音对话 SDK 是基于豆包端到端实时语音大模型，使用设备自身的录音机与播放器，提供低延迟、双向流式语音交互能力，可用于构建语音到语音的对话工具。\\\"}]\"}"];
if (ret != SENoError) {
    NSLog(@"RAG Query失败，返回值: %d", ret);
    return;
}
```

<span id="334ae110"></span>
### 自定义音频输入
Dialog 可以选择不使用系统录音机，而通过feedAudio接口，传入原始音频数据作为用户的输入音频。注意：

* 输入音频仅支持 PCM 16bit位深音频格式，且采样率需要为16k，通道数为1即单通道。
* 如果采样率不为16k，或者通道数不为1，可以开启SDK 内部重采样，SDK会自动重采为16k、单通道音频。

```objectivec
//【可选配置】配置音频来源，STREAM 模式表示使用自定义音频输入
[self.engine setStringParam:SE_RECORDER_TYPE_STREAM forKey:SE_PARAMS_KEY_RECORDER_TYPE_STRING];
// 当输入音频采样率不为16k，或通道数不为1时，使用下述方式开启重采样
// 【可选配置】启用 SDK 内部的重采样，默认不启用。
// [self.engine setBoolParam:TRUE forKey:SE_PARAMS_KEY_ENABLE_RESAMPLER_BOOL];
// 【可选配置】设置为输入音频的实际采样率，这里以采样率44.1k 通道数2 为例，sdk内部会重采为采样率16k 通道1。
// [self.engine setIntParam:44100 forKey:SE_PARAMS_KEY_CUSTOM_SAMPLE_RATE_INT];
// [self.engine setIntParam:2 forKey:SE_PARAMS_KEY_CUSTOM_CHANNEL_INT];

// ...
// 初始化引擎实例 并 发送指令-启动引擎
// ...

int bufferLen = 32000;
int16_t* buffer = (int16_t*) malloc(bufferLen * sizeof(int16_t));

// ...
// 业务自行将音频读入到 buffer 中
// ...
SEEngineErrorCode ret = [self.engine feedAudio:buffer length:bufferLen];
if (ret != 0) {
    NSLog(@"Feed audio failed: %d", ret);
    return;
}
```

<span id="b9b32b6a"></span>
### 自定义音频输出
Dialog 可以关闭设备播放器，业务方通过回调拿到原始音频数据，再视需要进行使用。

* 播放器数据回调：即使关闭播放器，也会跟随现实时间播放进度，每100ms现实时间就回调100ms的音频数据。
* 解码器数据回调：不跟随现实时间，只要收到Dialog服务的音频数据并解码后，就立刻回调接收到的所有音频数据。

```objectivec
//【可选配置】是否开启播放器，默认开启，不使用设备播放器时需要关闭。
[self.engine setBoolParam:FALSE forKey:SE_PARAMS_KEY_DIALOG_ENABLE_PLAYER_BOOL];
//【可选配置】启用播放器音频回调，默认不启用（为当前正在播放的音频数据，会随着播放进度回调）
[self.engine setBoolParam:TRUE forKey:SE_PARAMS_KEY_DIALOG_ENABLE_PLAYER_AUDIO_CALLBACK_BOOL];
//【可选配置】启用解码后原始音频回调，默认不启用（为解码后的需要播报的数据，会在解码完成后立刻回调，不等待播放进度）
[self.engine setBoolParam:TRUE forKey:SE_PARAMS_KEY_DIALOG_ENABLE_DECODER_AUDIO_CALLBACK_BOOL];

// ...
// 初始化引擎实例 并 发送指令-启动引擎
// ...

// 消息回调中，对接收到的音频数据进行处理
- (void)onMessageWithType:(SEMessageType)type andData:(NSData *)data {
    switch (type) {
        case SEDialogPlayerAudio:
            // Callback: 播放器音频数据回调
            NSLog(@"Callback: 播放器音频数据回调");
            break;
        case SEDecoderAudioData:
            // Callback: 解码器音频数据回调
            NSLog(@"Callback: 解码器音频数据回调");
            break;
        default:
            break;
    }
}
```

<span id="5e036517"></span>
### 客户端打断
```objectivec
SEEngineErrorCode ret = [self.speechEngine sendDirective:SEDirectiveEventClientInterrupt data:@"{}"];
if (ret != SENoError) {
    NSLog(@"Send directive client interrupt failed: %d", ret);
} else {
    NSLog(@"Send directive client interrupt succeed");
}
```

<span id="ca3f4bf3"></span>
### 更新通话过程中的SP相关配置
```objectivec
SEEngineErrorCode ret = [self.speechEngine sendDirective:SEDirectiveEventUpdateConfig data:@"{\"dialog\":{\"bot_name\":\"test\"}}"];
if (ret != SENoError) {
    NSLog(@"Send directive update config failed: %d", ret);
} else {
    NSLog(@"Send directive update config succeed");
}
```

<span id="98666fc6"></span>
### 暂停播放
```objectivec
SEEngineErrorCode ret = [self.speechEngine sendDirective:SEDirectivePausePlayer];
if (ret != SENoError) {
    NSLog(@"Send directive pause player failed: %d", ret);
} else {
    NSLog(@"Send directive pause player succeed");
}
```

<span id="6c6b5955"></span>
### 恢复播放
```objectivec
SEEngineErrorCode ret = [self.speechEngine sendDirective:SEDirectiveResumePlayer];
if (ret != SENoError) {
    NSLog(@"Send directive resume player failed: %d", ret);
} else {
    NSLog(@"Send directive resume player succeed");
}
```

<span id="50e1ee22"></span>
### 暂停录音
暂停麦克风录音，需要在启动引擎的时候，设置dialog.exra.input_mode为"keep_alive"
暂停麦克风录音时，会打断当前的ASR任务
```objectivec
SEEngineErrorCode ret = [self.speechEngine sendDirective:SEDirectivePauseRecorder];
if (ret != SENoError) {
    NSLog(@"Send directive pause recorder failed: %d", ret);
} else {
    NSLog(@"Send directive pause recorder succeed");
}
```

<span id="a8e51e2c"></span>
### 恢复录音
```objectivec
SEEngineErrorCode ret = [self.speechEngine sendDirective:SEDirectiveResumeRecorder];
if (ret != SENoError) {
    NSLog(@"Send directive resume recorder failed: %d", ret);
} else {
    NSLog(@"Send directive resume recorder succeed");
}
```

<span id="297a5eda"></span>
### 停止引擎
```objectivec
// 会等待回调函数执行完成，不可在回调线程中执行。
[self.engine sendDirective:SEDirectiveSyncStopEngine];
```

<span id="38f1108c"></span>
## 回调接收返回数据
启动引擎后，SDK会不断回调消息。其中Dialog类回调的消息内容，请参考[端到端实时语音大模型API接入文档--豆包语音-火山引擎](https://www.volcengine.com/docs/6561/1594356) 中 服务事件 说明。
```objectivec
- (void)onMessageWithType:(SEMessageType)type andData:(NSData *)data {
    NSLog(@"Message Type: %d.", type);
    NSString *strData = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
    switch (type) {
        case SEEngineStart:
            // Callback: 引擎启动成功回调
            NSLog(@"Callback: 引擎启动成功: %@", strData);
            break;
        case SEEngineStop:
            // Callback: 引擎关闭回调，启动引擎成功后，此消息必定发生且为最后一个回调消息
            NSLog(@"Callback: 引擎关闭: %@", strData);
            break;
        case SEEngineError:
            // Callback: 错误信息回调
            NSLog(@"Callback: 错误信息: %d, data: %@", type, strData);
            break;
        case SEDialogASRInfo:
            // Callback: ASR语音识别开始,用户开始说话
            NSLog(@"Callback: ASR 识别开始");
            break;
        case SEDialogASRResponse:
            // Callback: ASR语音识别结果回调
            NSLog(@"Callback: ASR 识别结果: %@", strData);
            break;
        case SEDialogASREnded:
            // Callback: ASR语音识别结束，用户停止说话
            NSLog(@"Callback: ASR 识别结束");
            break;
        case SEDialogChatResponse:
            // Callback: Chat对话内容回调
            NSLog(@"Callback: Chat 回复内容: %@", strData);
            break;
        case SEDialogChatEnded:
            // Callback: Chat回复结束
            NSLog(@"Callback: Chat 回复结束");
            break;
        default:
            break;
    }
}
```

<span id="e61f3cb8"></span>
## 销毁引擎实例
当不再需要语音对话后，建议对引擎实例进行销毁，释放内存资源。
```Java
// 内部会执行SYNC_STOP，不可在回调线程中执行。
[self.engine destroyEngine];
self.engine = nil;
```

<span id="95fbcd22"></span>
# 示例工程

<Attachment link="https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/a92ff3509c404f9cabdb57d47e5aac4a~tplv-goo7wpa0wc-image.image" name="SpeechDemoIOS.zip" ></Attachment>


<span id="93bd4755"></span>
## 运行方法

1. 首先修改 Podfile 文件中的SDK依赖版本，添加TTNet 依赖，并执行 `pod install --repo-update`：

<div style="text-align: center"><img src="https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/9e9ea11100a548d8b2829acdbba03480~tplv-goo7wpa0wc-image.image" width="1608px" /></div>


2. XCode 打开 SpeechDemo.xcodeproj ，要运行语音对话功能，至少需修改SensitiveDefines.m 代码中下述内容：

其中 APPID 、APPKEY、TOKEN 获取方式参考 [控制台使用FAQ-Q1](https://www.volcengine.com/docs/6561/196768#q1%EF%BC%9A%E5%93%AA%E9%87%8C%E5%8F%AF%E4%BB%A5%E8%8E%B7%E5%8F%96%E5%88%B0%E4%BB%A5%E4%B8%8B%E5%8F%82%E6%95%B0appid%EF%BC%8Ccluster%EF%BC%8Ctoken%EF%BC%8Cauthorization-type%EF%BC%8Csecret-key-%EF%BC%9F)
<div style="text-align: center"><img src="https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/24665410748e45af8bbc8cd4594296be~tplv-goo7wpa0wc-image.image" width="1122px" /></div>

RESOURCE ID 设置为 `volc.speech.dialog`。
<div style="text-align: center"><img src="https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/6a3a7c0ae5ee4994837a3414b5672f9f~tplv-goo7wpa0wc-image.image" width="1254px" /></div>


3. 修改所有代码配置后，配置SpeechDemo为开发者自己的签名，然后点击XCode 三角号执行demo 构建运行即可。

<div style="text-align: center"><img src="https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/1735d6c6626a44feab54efcc785d5147~tplv-goo7wpa0wc-image.image" width="1702px" /></div>


4. 选择 语音交互 -> 对话交互 进入Dialog 测试页，对应的页面代码文件为 SpeechDemo/DialogViewController.m

<div style="text-align: center"><img src="https://p9-arcosite.byteimg.com/tos-cn-i-goo7wpa0wc/09022defb96849b78b22b274691c3097~tplv-goo7wpa0wc-image.image" width="200px" /></div>


5. 点击 Init Engine -> Start Engine 即可体验语音对话功能。
