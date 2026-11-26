const card = document.querySelector("[data-jlpt-card]");
if (!card) return;

const style = getComputedStyle(card);
const root = document.documentElement;
root.dataset.jlptDayTheme = style.getPropertyValue("--jlpt-day-theme").trim();
root.dataset.jlptNightTheme = style.getPropertyValue("--jlpt-night-theme").trim();

const traditional = style.getPropertyValue("--jlpt-language").trim() === "zh-Hant";
for (const simplified of card.querySelectorAll("[data-jlpt-hans]")) {
  simplified.hidden = traditional && simplified.nextElementSibling?.hasAttribute("data-jlpt-hant");
}
for (const alternative of card.querySelectorAll("[data-jlpt-hant]")) {
  alternative.hidden = !traditional;
}

const level = card.querySelector(".Level");
if (level) {
  level.textContent = level.textContent.trim().split(/\s+/)
    .map((tag) => tag.split("::").at(-1))
    .filter((tag) => !/^v\d+\./.test(tag)).join("・");
}

for (const text of card.querySelectorAll('.JLPTPrompt[lang="ja"], .SentKanji, .SentFurigana')) {
  text.innerHTML = text.innerHTML.replace(/(<[^>]+>)|\s+/g, (match, tag) => tag || "");
}

const lookup = card.querySelector(".Search");
if (lookup) {
  const classes = document.documentElement.className;
  const dictionary = classes.includes("ios") || classes.includes("android") ? "moji"
    : classes.includes("mac") ? "dict"
    : classes.includes("win") ? "goldendict" : "weblio";
  const word = card.querySelector(".VocabKanji [lang='ja']").cloneNode(true);
  word.querySelectorAll("rt").forEach((reading) => reading.remove());
  lookup.href = dictOptions[dictionary].replace("{query}", encodeURIComponent(word.textContent.trim()));
}

for (const sentence of card.querySelectorAll("[data-jlpt-type]")) {
  const symbol = sentence.dataset.jlptType || "例";
  const expression = sentence.querySelector(".SentFurigana");
  const label = document.createElement("em");
  label.lang = "ja";
  label.textContent = `［${symbol}］`;
  expression.prepend(label);
  if (symbol === "関") expression.classList.add("JLPTSynonym");
  if (symbol === "対") expression.classList.add("JLPTOpposite");
}

// Format only the cue for the active cloze.
for (const hint of card.querySelectorAll(".JLPTHint[data-cloze-hint]")) {
  const cue = hint.querySelector(`[data-jlpt-answer-hint="${hint.dataset.clozeHint}"]`);
  if (!cue) continue;
  const text = cue.textContent.trim();
  const lines = text.split(/(?=第\s*\d+\s*空\s)/);
  if (lines.length > 1) {
    cue.replaceChildren(...lines.filter(line => line.trim()).map(line => {
      const row = document.createElement("span");
      row.className = "JLPTClozeHintLine";
      row.textContent = line.trim();
      return row;
    }));
  }
}

const controls = card.querySelectorAll("[data-jlpt-audio]");
if (!controls.length) return;
const configuredRate = Number(style.getPropertyValue("--jlpt-replay-rate"));
const rate = configuredRate >= 0.5 && configuredRate <= 2 ? configuredRate : 1;
let audio;
const observer = new MutationObserver(() => {
  if (card.isConnected) return;
  audio?.pause();
  observer.disconnect();
});
observer.observe(document.body, {childList: true, subtree: true});

for (const control of controls) {
  const native = control.hasAttribute("data-jlpt-native");
  const embedded = control.querySelector("audio");
  if (embedded) {
    embedded.controls = false;
    const icon = control.querySelector("template");
    if (icon && !control.querySelector(".replay-button")) {
      control.append(icon.content.cloneNode(true));
    }
  }
  let useNativeFallback = false;
  control.addEventListener("click", async (event) => {
    const icon = event.target.closest(".replay-button, .soundLink");
    if (!icon || useNativeFallback) return;
    audio?.pause();
    const filename = control.dataset.jlptAudio;
    if (native && !embedded && (rate === 1 || !filename)) return;
    event.preventDefault();
    event.stopImmediatePropagation();

    const message = control.querySelector("[data-jlpt-audio-error]");
    message.hidden = true;
    audio = embedded || new Audio(encodeURIComponent(filename));
    try {
      audio.currentTime = 0;
      audio.playbackRate = rate;
      await audio.play();
    } catch {
      if (!card.isConnected) return;
      if (native && !embedded) {
        useNativeFallback = true;
        icon.click();
        useNativeFallback = false;
      } else {
        message.hidden = false;
      }
    }
  }, true);
}
