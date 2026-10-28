# PPT Training Video

用阿里云百炼理解单份/两份 PPT，生成逐页讲稿与配音，按**实际音轨时长**翻页，合成包含中文字幕、声音与虚拟讲师的 MP4。

- Python 命令行，**uv 管理依赖**；无前端、无 Docker。
- 支持 `.ppt` / `.pptx`，Windows 本地开发、Linux 部署。
- 视觉理解与讲稿默认：`qwen3.7-plus-2026-05-26`（百炼图文输入）。
- 低成本配音默认：`cosyvoice-v3-flash` / `longanyang`，原生 HTTP API。
- 默认原创 2D 卡通讲师，嘴部按真实配音音量开合，静音闭嘴、定时眨眼。**不是音素级对口型**。
- 支持自备真人循环视频，其原音轨会被丢弃，嘴型不匹配当前讲稿。
- 双 PPT 默认**以后者的页面顺序和内容为主**，介绍相对前者的差异；支持自定义侧重点。
- 默认缓存成功阶段；可先导出/编辑讲稿后继续；支持局部重生成。

## 1. 安装（Windows）

使用 PowerShell：

```powershell
# uv 官方安装器，也可用 winget install --id astral-sh.uv -e
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
# 关闭并重新打开终端，确认 PATH 生效
uv --version

# 安装 LibreOffice（用于旧 PPT 转换、原始页图渲染）
winget install --id TheDocumentFoundation.LibreOffice -e

git clone https://github.com/ziggycloud/ppt-training-video.git
cd ppt-training-video
uv python install 3.12
uv sync --locked
uv run ppt-trainer init --config config.yaml
```

编辑 `config.yaml`，填入 Key 和 Base URL；必要时设置：

```yaml
tools:
  libreoffice: "C:/Program Files/LibreOffice/program/soffice.exe"
subtitles:
  font_path: "C:/Windows/Fonts/msyh.ttc"
```

**YAML 的 Windows 路径建议使用 `/`，或单引号包裹反斜杠路径**，避免 `\t`、`\n` 被解析成转义。

```powershell
uv run ppt-trainer doctor
uv run ppt-trainer generate --ppt "D:/培训/课程.pptx" --output "output/课程.mp4"
```

无需单独安装 FFmpeg，`imageio-ffmpeg` 提供匹配平台的二进制。如果企业环境必须使用自备 FFmpeg，设置 `tools.ffmpeg`，需启用 H.264/AAC 和 `libass/subtitles`。

## 2. 安装（Linux，Ubuntu/Debian）

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
# 重新打开终端，或加载安装器提示的环境脚本
export PATH="$HOME/.local/bin:$PATH"

sudo apt-get update
sudo apt-get install -y libreoffice-impress fonts-noto-cjk fontconfig git

git clone https://github.com/ziggycloud/ppt-training-video.git
cd ppt-training-video
uv python install 3.12
uv sync --locked
uv run ppt-trainer init --config config.yaml
# 编辑 config.yaml
uv run ppt-trainer doctor
uv run ppt-trainer generate --ppt /data/course.pptx --output /data/output/course.mp4
```

Linux 无需桌面/显示器；LibreOffice 以 headless 和隔离用户配置目录运行。其他发行版安装对应的 LibreOffice Impress 与中文字体包。可将 `subtitles.font_path` 设为 `/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc`。

如果 `uv python install` 无法访问下载源，可用已有的 Python 3.11+：`uv sync --python /path/to/python --locked`。首次同步依赖需要网络；之后可使用预热缓存和 `uv run --offline`。uv 安装依据：[官方文档](https://docs.astral.sh/uv/getting-started/installation/)。

## 3. 本地 YAML 配置

`uv run ppt-trainer init` 生成的 `config.yaml` 默认被 Git 忽略。仓库只提交 `config.example.yaml`。

```yaml
bailian:
  api_key: "你自己的Key"
  chat_base_url: "https://你的业务空间ID.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
  native_base_url: "https://你的业务空间ID.cn-beijing.maas.aliyuncs.com/api/v1"
  model: "qwen3.7-plus-2026-05-26"
```

- `chat_base_url` **不带** `/chat/completions`；程序自行拼接。
- `native_base_url` **不带** `/services/audio/tts/SpeechSynthesizer`。
- 两类地址和 Key 必须属于正确地域/业务空间；自定义代理也必须实现对应协议。
- 可用 `DASHSCOPE_API_KEY` 环境变量覆盖 YAML Key。不要把 Key 放在命令行参数、截图或 issue 中。
- 快照模型不可用时，可在控制台核对后换成 `qwen3.7-plus` 等支持图像输入和 JSON Schema 的模型。不可直接改为纯文本模型。
- 本项目使用原生 HTTP TTS 协议。`qwen3-tts-flash` 属于另一套协议，**不能只修改 model 名称直接套用**。默认 `cosyvoice-v3-flash` 的系统音色要匹配模型。[TTS 接口](https://help.aliyun.com/en/model-studio/cosyvoice-tts-http-api)、[音色表](https://help.aliyun.com/zh/model-studio/cosyvoice-voice-list)。

完整字段见 [config.example.yaml](config.example.yaml)。常用参数：

| 字段 | 作用 |
|---|---|
| `course.target_minutes` | 讲稿长度规划目标；最终长度以实际配音为准，不硬截断/拉伸凑时长 |
| `course.characters_per_minute` | 字符预算，默认220；和语速一起调节 |
| `course.audience` / `extra_instructions` | 培训对象与事实约束 |
| `tts.rate` / `voice` | 语速、音色（包括兼容的复刻 voice_id） |
| `tts.max_chars_per_request` | 每条配音上限，实际还受字幕容纳字数限制 |
| `tts.pause_after_slide_seconds` | 每页末尾静音停顿 |
| `avatar.mode` | `cartoon`、`loop` 或 `none` |
| `avatar.video_path` | 循环视频路径，相对路径以 YAML 所在目录为准 |
| `avatar.side` / `width_fraction` | 人物位置与宽度比例 |
| `video.width` / `height` / `fps` | 分辨率、帧率，宽高必须为偶数 |
| `video.crf` / `preset` / `threads` | 编码质量、速度和CPU线程 |
| `subtitles.*` | 字体、字号、每行字数、颜色、开关 |
| `cache_dir` | 本地缓存目录；包含课程数据，不要公开提交 |

## 4. 单 PPT 一条命令生成

```bash
uv run ppt-trainer generate \
  --config config.yaml \
  --ppt "课程.pptx" \
  --target-minutes 20 \
  --output "output/课程.mp4"
```

Windows PowerShell 可把命令写成一行，或用反引号换行；上述 `\` 是 Bash 换行符。

输出：

```text
output/课程.mp4            # H.264 + AAC，默认1080p / 25fps
output/课程.srt            # UTF-8 BOM 外挂字幕
output/课程.manifest.json  # 实际时长、逐页时间线、模型用量与哈希
output/课程.plan.json      # 可检查/编辑的讲稿与事实引用
```

默认自动执行全流程；正式规培材料建议先审核讲稿（见下一节）。模型不能替代业务/临床专家核对。

## 5. 先检查讲稿，再生成视频

```bash
uv run ppt-trainer generate --ppt "课程.pptx" --plan-only --output "output/课程.mp4"
# 编辑 output/课程.plan.json 中各 scene 的 narration，保持 fact_refs/来源信息有效。
uv run ppt-trainer render --plan "output/课程.plan.json" --output "output/课程.mp4"
```

`render` 不重新调用 PPT 理解模型，仅继续配音和渲染。图片位于本地缓存目录，请保留缓存；跨机器迁移时需一并复制图片，并更新计划中的 `image_path`。字幕与配音使用同一 narration，默认不做会造成索引错位的自动读音替换；型号可在讲稿中人工改为明确可读形式。

## 6. 两份 PPT 对比

```bash
# 前者A为旧版/参考，后者B为主要内容与视频页面
uv run ppt-trainer generate --ppt "旧版.ppt" --compare "新版.pptx" --output "output/改款培训.mp4"

# 自定义侧重点
uv run ppt-trainer generate --ppt "产品A.pptx" --compare "产品B.pptx" \
  --focus "面向售后人员，优先解释安装步骤、兼容性和故障处理变化，略讲营销卖点。" \
  --output "output/售后对比.mp4"

# 多行提示词推荐保存为 UTF-8 文件
uv run ppt-trainer generate --ppt "旧版.pptx" --compare "新版.pptx" \
  --focus-file "focus.txt" --plan-only --output "output/对比.mp4"
```

`--focus` 与 `--focus-file` 互斥。优先级：CLI > YAML `course.focus` > 默认策略。传入 `--focus ""` 使用默认策略。

双资料分别进行图文理解，再按主题产生差异依据；旧版用于参考，输出始终显示新版页面。小于等于30页的旧版提供全量事实；更大资料使用字符二元组候选召回 + 全量主题目录，允许模型标记不确定，不把“未召回”视为“功能不存在”。**这不是经过业务标注集验收的完美语义匹配器**；需要展示旧版原图的复杂对照版式不在首版中。

差异必须引用实际 fact_id；区分 changed / not_mentioned / conflict / uncertain。模型仍可能误读图表/单位，关键变更务必审核。

## 7. 两种低成本讲师

### 默认 2D 卡通

```yaml
avatar:
  mode: cartoon
  side: right
  width_fraction: 0.22
  coat_color: "#516bd4"
```

本项目代码绘制的原创卡通角色，无需额外图片生成 API。根据当前音轨短时 RMS 音量控制嘴部，静音时闭嘴，定时眨眼。CPU 即可编码，不训练人物模型，不按人物视频秒数付费。它是示意嘴部运动，**不宣称音素级口型或真人自然动作**。

### 自备真人循环视频

```yaml
avatar:
  mode: loop
  video_path: "assets/presenter.mp4"
  side: right
  width_fraction: 0.22
```

循环播放已有素材，丢弃素材原音轨，只保留课程 TTS。画面以矩形画中画显示，不自动抠像；口型与课程无关，静音时素材也可能继续动。请使用自有/有授权素材。首次素材制作费用不包含在本项目运行成本中。

## 8. 翻页、字幕与同步是怎样实现的

1. 每页讲稿按标点拆成有限长度短句，尽量保留产品标识和小数。
2. 每个短句调用一次 TTS，下载并解码成统一 PCM WAV，测量准确样本数。
3. 每个字幕的起止时间使用该短句音频的实际长度；**不是字级卡拉OK字幕，也不是按字数推算每个字的时间**。
4. 同页音频按顺序拼接，加页末停顿，再补齐到整数视频帧边界；音频与页面时长使用同一时间表。
5. 每页渲染无声视频片段，最后拼接所有画面，完整主音轨只编码/混入一次，避免每页AAC延迟累计和双重声音。
6. 修改某页讲稿后，变动短句重新配音、该页重新渲染，后续翻页与字幕偏移重新计算。

生成 `.manifest.json` 可核对每页 start/end 和真实总时长。目标20分钟只是文本预算，事实不足不会自动编造内容凑满，也不会截掉讲解以强行满足时长。

## 9. 缓存、错误与费用

- 解析缓存按源文件哈希与画面尺寸区分；理解/讲稿按模型、提示词和输入区分。
- 音频缓存按模型、音色、语速和短句区分；视频缓存还包含人物素材、字体及布局哈希。
- API Key 不落入缓存内容；缓存命名使用隔离摘要。输出 manifest 仅记录模型、用量、request_id，不保存 Key 或签名下载地址。
- 默认复用；`--refresh` 会重新调用模型，可能重复计费。
- 结构错误最多修复一次，截断和无效事实引用停止；401/403等错误不会无限重试。
- POST 网络超时不自动重复提交，因为服务端可能已经收费。429/502/503/504按配置退避，仍应留意供应商用量。
- “调用失败”的费用规则以供应商为准。成功生成但结果不满意，再生成通常仍收费。
- 模型Key未配置时程序明确报错，**不会偷偷改用本地示例声音或假讲稿**。

已准备稿件和形象时，主要API开销为TTS字符费用；图文理解按输入输出Token计费。具体价格以[百炼价格表](https://help.aliyun.com/zh/model-studio/model-pricing)为准，本仓库不承诺固定每条成本。CPU渲染、下载带宽、固定服务器与人工审核另计。

## 10. 测试与开发

```bash
uv sync --locked
uv run pytest -m "not integration"
uv run pytest                 # 包含真实FFmpeg与LibreOffice检查；缺少依赖时对应测试跳过
```

测试使用 MockTransport 模拟百炼JSON响应和**明确标注的测试音调**，不会调用收费API。集成测试解码实际生成MP4，检查2D/循环模式、页边界、缓存复用、局部修改，并用LibreOffice实际转换 `.pptx`/`.ppt`。Windows/Linux CI结果以仓库 Actions 为准；本地通过不等于两个系统均已验证。

未提供用户Key，因此**真实百炼连通性、当前模型权限与声音效果尚未验证**。接入后先用2–3页短课验证，再跑20分钟任务。`doctor` 仅检查本地依赖，不发起收费请求。

目录：

```text
src/ppt_trainer/
  config.py       YAML与参数校验
  deck.py         OOXML、旧PPT转换、原页渲染
  api.py          百炼视觉JSON/原生HTTP TTS
  planning.py     事实、双资料差异与逐页讲稿
  audio.py        短句配音、PCM缓存、精确时长
  render.py       2D音量动画、循环视频、ASS字幕、FFmpeg
  pipeline.py     时间线、缓存恢复、最终MP4与工程输出
  cli.py          命令行入口
```

## 11. 公开仓库与数据保护

仅提交源码、锁文件、空白配置模板、文档与测试。实际 YAML、课件、肖像、录音、视频、日志、缓存均不应公开提交。默认 `.gitignore` 已覆盖常见路径与媒体扩展，但重命名文件或自建目录仍需自己检查。

课件页图使用 Base64 送至你配置的百炼/代理；讲稿送至TTS。**配置的服务端会收到课件与讲稿内容**，请按单位数据要求选择接入地域和供应商。没有上传到其他第三方图像/数字人服务。

API Key保存在本地YAML是按本项目需求提供的便利方式；生产服务器建议用环境变量覆盖，并限制配置文件读取权限。不要将包含真实Key的配置发到公开issue。

## 官方接口参考

- [视觉模型](https://help.aliyun.com/zh/model-studio/vision-model/)
- [结构化JSON输出](https://help.aliyun.com/zh/model-studio/qwen-structured-output)
- [CosyVoice原生HTTP接口](https://help.aliyun.com/en/model-studio/cosyvoice-tts-http-api)
- [CosyVoice音色](https://help.aliyun.com/zh/model-studio/cosyvoice-voice-list)
- [uv安装](https://docs.astral.sh/uv/getting-started/installation/)

## License

MIT（本项目源码和程序绘制的卡通角色）。Python依赖、LibreOffice、字体及FFmpeg各自保留原许可。PDF 渲染使用 pypdfium2；FFmpeg 二进制包含的编码器可能影响分发条件，分发前请确认依赖授权。本项目 MIT 不覆盖第三方依赖。
