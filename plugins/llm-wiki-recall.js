import { execFileSync } from "child_process";
import { existsSync, mkdirSync, appendFileSync } from "fs";
import { homedir } from "os";
import { join, dirname } from "path";

const wikiRoot = () => process.env.LLM_WIKI_ROOT || join(homedir(), "llm-wiki");
const hasWiki = () => existsSync(join(wikiRoot(), "wiki", "index.md"));
const enrichBin = join(homedir(), ".local", "bin", "llm-wiki-enrich");
const probeLog = join(homedir(), ".opencode", "llm-wiki-recall.log");

const log = (msg) => {
  try {
    mkdirSync(dirname(probeLog), { recursive: true });
    appendFileSync(probeLog, `${new Date().toISOString()} ${msg}\n`);
  } catch {
    void 0;
  }
};

const digest = () => {
  if (!existsSync(enrichBin)) return "";
  try {
    return execFileSync(enrichBin, ["--session-start", "--max-chars", "8000"], {
      encoding: "utf8",
      timeout: 5000,
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
  } catch {
    return "";
  }
};

// Gap B: just-in-time 语义召回。用本条 prompt 文本 query top wiki 页，严控预算防污染。
// 空召回（enrich 输出 "No matching..."）返回空串，调用方据此跳过注入。
const recall = (query) => {
  if (!existsSync(enrichBin) || !query || query.length < 4) return "";
  try {
    const out = execFileSync(
      enrichBin,
      ["--query", query, "--limit", "3", "--max-chars", "2000"],
      { encoding: "utf8", timeout: 5000, stdio: ["ignore", "pipe", "ignore"] },
    ).trim();
    if (!out || out.includes("No matching local memory context")) return "";
    return out;
  } catch {
    return "";
  }
};

// 从用户消息 parts 里抽取可用作 query 的文本，跳过本插件注入的 synthetic part。
const promptText = (parts) => {
  if (!Array.isArray(parts)) return "";
  return parts
    .filter((p) => p && p.type === "text" && !p.synthetic && typeof p.text === "string")
    .map((p) => p.text)
    .join("\n")
    .slice(0, 2000)
    .trim();
};

// Gap D: compact 前把本会话累积的 prompt 落一份草稿到 raw/inbox/auto-drafts/。
// 仅落 inbox 草稿，绝不进 wiki —— 保留手动 SessionCapture 的人审闸门与信噪比。
const writeAutoDraft = (sessionID, prompts) => {
  if (!prompts.length) return;
  const dir = join(wikiRoot(), "raw", "inbox", "auto-drafts");
  try {
    mkdirSync(dir, { recursive: true });
    // 用本地时区算文件名日期（en-CA 给 YYYY-MM-DD）；UTC 会让东八区凌晨落到前一天文件。
    const day = new Date().toLocaleDateString("en-CA");
    const file = join(dir, `${day}.md`);
    const stamp = new Date().toISOString();
    const sid = String(sessionID || "unknown").slice(0, 24);
    const body =
      `\n## auto-draft ${stamp} (session ${sid})\n` +
      `> 自动草稿，compact 前落盘。**未经人审，不是 wiki 内容**。复盘时决定取舍。\n\n` +
      prompts.map((p, i) => `${i + 1}. ${p.replace(/\n+/g, " ").slice(0, 300)}`).join("\n") +
      "\n";
    appendFileSync(file, body);
    log(`session.compacting WROTE auto-draft (${prompts.length} prompts) -> ${file}`);
  } catch (e) {
    log(`auto-draft write failed: ${e?.message || e}`);
  }
};


export const LlmWikiRecallPlugin = async () => {
  // 工厂在每个 opencode 进程内只调用一次，故状态必须按 sessionID 隔离，
  // 否则 injected 会让同进程内的第二个 session 拿不到 session-start 注入，
  // 且 sessionPrompts 会把不同 session 的 prompt 混进同一份 compact 草稿。
  const MAX_PROMPTS = 200;
  const sessions = new Map();
  const stateFor = (sessionID) => {
    const key = String(sessionID || "default");
    let s = sessions.get(key);
    if (!s) {
      s = { injected: false, prompts: [] };
      sessions.set(key, s);
    }
    return s;
  };
  log(`plugin loaded (pid=${process.pid})`);

  const pushPart = (output, input, text) => {
    const sibling = output.parts[0];
    output.parts.push({
      id: `prt_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`,
      sessionID: sibling.sessionID ?? input.sessionID,
      messageID: sibling.messageID ?? input.messageID,
      type: "text",
      text,
      synthetic: true,
    });
  };

  return {
    "chat.message": async (input, output) => {
      if (!hasWiki() || !Array.isArray(output?.parts) || output.parts.length === 0) return;
      const sid = output.parts[0]?.sessionID ?? input?.sessionID;
      const state = stateFor(sid);
      log(`chat.message fired (session=${String(sid).slice(0, 8)}, injected=${state.injected}, parts=${output.parts.length})`);

      // Gap A: 会话级全量注入，每 session 一次。
      if (!state.injected) {
        const text = digest();
        if (text) {
          pushPart(output, input, text);
          state.injected = true;
          log("chat.message INJECTED synthetic digest part");
        }
      }

      // Gap B: prompt 级语义召回，每条 prompt 都跑（独立于会话级标志）。
      const q = promptText(output.parts);
      const hits = recall(q);
      if (hits) {
        pushPart(output, input, hits);
        log("chat.message INJECTED just-in-time recall part");
      }

      // Gap D: 累积本 session 用户 prompt，供 compact 前落草稿；限界防超长会话内存膨胀。
      if (q) {
        state.prompts.push(q);
        if (state.prompts.length > MAX_PROMPTS) state.prompts.splice(0, state.prompts.length - MAX_PROMPTS);
      }
    },

    "experimental.session.compacting": async (input, _output) => {
      const sid = input?.sessionID;
      const state = stateFor(sid);
      log(`session.compacting fired (session=${String(sid).slice(0, 8)}, prompts=${state.prompts.length})`);
      if (!hasWiki()) return;
      writeAutoDraft(sid, state.prompts);
      state.prompts.length = 0;
    },

    "experimental.chat.system.transform": async (input, output) => {
      if (!hasWiki() || !Array.isArray(output?.system)) return;
      const state = stateFor(input?.sessionID);
      log(`system.transform fired (injected=${state.injected}, system=${output.system.length})`);
      if (state.injected) return;
      const text = digest();
      if (!text) return;
      output.system.push(text);
      state.injected = true;
      log("system.transform INJECTED digest into system[]");
    },

    "tool.execute.before": async (input, output) => {
      if (input.tool !== "bash" || !output?.args?.command || !hasWiki()) return;
      const state = stateFor(input?.sessionID);
      log(`tool.execute.before fired (tool=${input?.tool}, injected=${state.injected})`);
      if (state.injected) return;
      // `|| true` keeps a missing/failing enrich from ever breaking the user's command.
      output.args.command =
        '{ command -v llm-wiki-enrich >/dev/null 2>&1 && ' +
        'llm-wiki-enrich --session-start --max-chars 8000 || true; } ; ' +
        output.args.command;
      state.injected = true;
      log("tool.execute.before INJECTED enrich prefix into bash");
    },
  };
};
