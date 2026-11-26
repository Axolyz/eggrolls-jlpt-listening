# JLPT 例句听力 · 和色版

基于 egg rolls 的 `26.11.20` 版，优先用有音频的第一条例句训练听力，按读音、用法和中文词义干扰加开文字卡。可练形→音、句中词义、自他配对和搭配完形。全量开卡规则见 [DECK-RULES.md](DECK-RULES.md)。

日间用真朱和鸟之子纸色，夜间用珊瑚朱和深靛蓝。Anki 卡面跟随客户端的日夜模式。 [NIPPON COLORS 日本の伝統色](https://nipponcolors.com/)

## 下载和导入

下载发行页里的 `jlpt-listening.apkg`，用 Anki 打开。第一次导入包含约 334 MB 的媒体。 [下载最新版](https://github.com/Axolyz/anki-jlpt-listening/releases/latest)

## 从上游构建

需要 Git 和 uv。构建使用独立的临时 Anki 数据库。生成的 `dist/jlpt-listening.apkg` 可在 Anki 里作为独立牌组导入，包含音频和字体。

```sh
git clone --depth 1 --branch 26.11.20 https://github.com/5mdld/anki-jlpt-decks.git ../anki-jlpt-decks
uv run build_deck.py --upstream ../anki-jlpt-decks
```

每次构建按上游的 `NoteID` 重算开关，保留等级和频率子牌组。可复用的人工校订集中在 `editorial.json`，绑定词条和用例。构建会检查人工选中的例句是否仍匹配，生成 `dist/deck-audit.json`，记录候选义项多于可用录音、重复录音、词音兜底等情况。牌组使用独立的笔记类型和稳定的笔记 GUID，便于后续导入新版。发布包无个人复习记录。

模板预览有 11 条笔记、29 张卡和 16 种模板，适合在独立的预览牌组中检查样式。

```sh
uv run preview.py --upstream ../anki-jlpt-decks
```

## 迁移旧魔改版的进度

`migrate_progress.py` 默认在集合副本上试跑。它按 `NoteID` 和实际考点匹配，听力额外核对录音内容。可查阅的本地报告列出旧卡、可选的新卡和匹配失败的原因。确认试跑结果后，在关闭 Anki 的情况下用 `--apply` 向目标集合导入并复制确定匹配的进度。脚本会在写入前生成集合备份。同一份历史会暂时出现在新旧卡上，裁决完成后可以在 Anki 里处理旧牌组。

## 全局设置

在 Anki 打开预览牌组任一卡片的「卡片模板 → 样式」，修改顶部的共用设置。

- `--jlpt-language` 默认 `zh-Hans`，可改为 `zh-Hant`。
- `--jlpt-input-display` 默认 `block`，显示形→音、形→意和完形的输入及对照。改成 `none` 可隐藏。形→意的输入对照以简中答案为基准，同义表述照常自评。
- `--jlpt-replay-rate` 默认 `1`，可改成 `0.9` 或 `1.15`。点击卡片播放图标使用这个倍率。Anki 原生自动播放和原生播放快捷键由客户端播放器管理。
- `--jlpt-day-theme` 默认 `shinsyu`（真朱）。另一套日间主题是 `sumi-paper`（墨と紙）。
- `--jlpt-night-theme` 默认 `kon`（紺 × 珊瑚朱）。其他夜间主题及其代号列在样式顶部。例如改成 `ink-byakugun` 就会在夜间使用「墨 × 白群」。

日间和夜间各选一套，Anki 根据客户端模式切换。同一笔记类型的所有卡片共用设置。

## 模板字段

- `Listening1`–`Listening4`、`WordListening` 控制听力卡。正常听力的正面只有题意和音频，反面首先显示所听例句。
- `ReadingPrompt` 与 `ReadingAnswer` 指定形→音题面和辞书形读音。模板使用 Anki 的输入对照。
- `Meaning1`–`Meaning4`、`WordMeaning` 可独立开形→意。例句背面显示完整译文；`TargetDefSCN` 用于句中词义的输入对照，支持部分匹配。例句缺译文时，可用 `TargetDefSCN` 和 `TargetDefTCN` 显示目标义。
- 第一、第二组完形支持 `((c1::…))` 和 `((c2::…))`。构建时按空位生成对应的 `Cloze…Answer`，同编号有多处空位时按顺序用 `／` 隔开。手动编辑完形时也要同步相应的答案字段。完形的提示默认可见。自他题用配对词、自动或他动用法及原句的写法限定要填的词；需要补写法时优先给首辅音或首假名。手动填写 `ClozeHint1` 或 `ClozeHint2` 时，一段普通提示适用于整组，也可按空号分别写 `<span data-jlpt-answer-hint="1">…</span>` 和编号为 `2` 的一段。中→日由 `Alt2` 控制。

## 来源

原牌组作者为 egg rolls。和色版沿用原始词汇与媒体，并为听力、词义辨析和完形修改了卡片模板、样式与开卡规则。原牌组适用 CC BY-NC 4.0，详见 [原牌组许可](UPSTREAM-LICENSE.txt)。完形使用 Matthew Hayes 的 Cloze Anything v0.3，适用 Apache 2.0。两套许可随构建包附带。 [egg rolls JLPT 牌组](https://github.com/5mdld/anki-jlpt-decks) · [Cloze Anything](https://github.com/matthayes/anki_cloze_anything/releases/tag/v0.3)
