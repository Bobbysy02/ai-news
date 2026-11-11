/**
 * AI 问答中转（腾讯云云函数 SCF · 事件函数 + 函数 URL）
 * ------------------------------------------------------------
 * 作用和 Cloudflare 版完全一样：保管 API key，网页只调用它的地址。
 * 换腾讯云的原因是：workers.dev 在国内被 DNS 污染，打不开。
 *
 * 部署要点（详见 README-AI问答部署.md）：
 *   函数类型：事件函数（不是 Web 函数）
 *   运行环境：Node.js 18 或以上
 *   提交方法：index.main_handler
 *   环境变量：ZHIPU_API_KEY = 你的智谱 key
 *   函数 URL：公网访问 + 授权类型选「开放」
 *
 * 为什么用 https 模块而不是 fetch：
 *   SCF 的 Node 版本不一定都带全局 fetch，用内置模块最稳，任何版本都能跑。
 */

const https = require("https");

// 允许调用本站函数的来源。默认锁死在自己的站点，
// 别人把自己的网页指向这个地址时，浏览器会因为跨域头不匹配而拒绝。
// 想放宽就在云函数环境变量里配 ALLOW_ORIGIN（多个用逗号分隔）。
// localhost / 127.0.0.1 单独放行，方便本地调试，不影响线上安全。
const ALLOW_GROUPS = (process.env.ALLOW_ORIGIN || "https://bobbysy02.github.io")
  .split(",")
  .map(function (s) { return s.trim(); })
  .filter(Boolean);
const LOCAL_PREFIX = ["http://localhost", "http://127.0.0.1", "file://"];

/** 请求来自哪个来源，就回哪个来源；不在白名单就回默认值（浏览器会拦掉） */
function pickOrigin(event) {
  var h = (event && event.headers) || {};
  var o = h.origin || h.Origin || "";
  if (!o) return ALLOW_GROUPS[0];
  for (var i = 0; i < ALLOW_GROUPS.length; i++) {
    if (o === ALLOW_GROUPS[i]) return o;
  }
  for (var j = 0; j < LOCAL_PREFIX.length; j++) {
    if (o.indexOf(LOCAL_PREFIX[j]) === 0) return o;
  }
  return ALLOW_GROUPS[0];
}

// 单次模型请求最多等多久。设短一点：
// 免费模型高峰期会「排队」，与其干等 22 秒不如快速失败换下一个。
const PER_MODEL_MS = 9000;
// 整轮最多花多久，必须小于云函数执行超时（30 秒），留出返回错误的时间
const TOTAL_BUDGET_MS = 24000;

// 进程内缓存：云函数实例复用期间，同一个问题直接返回，不再调模型。
// 只缓存「概念题」（没有带站内新闻上下文的），避免上下文不同却复用答案。
const CACHE = new Map();
const CACHE_MAX = 120;
const CACHE_TTL = 6 * 3600 * 1000;

const PROVIDERS = {
  zhipu: {
    url: "https://open.bigmodel.cn/api/paas/v4/chat/completions",
    keyName: "ZHIPU_API_KEY",
    // 免费模型轮询链：智谱免费模型经常 1305「访问量过大」，一个满了换下一个
    // noThink：该模型支持 "thinking":{"type":"disabled"}。
    //   GLM-4.7 系列默认开思考，一个「什么是 RAG」会先烧掉 600 多个推理 token，
    //   既慢（实测 17~27 秒）又把 max_tokens 吃空导致回答被切断。
    //   老模型（GLM-4）没有这个参数，传了会报错，所以单独标 null。
    models: [
      { id: "glm-4.7-flash", noThink: true },
      { id: "glm-4.5-flash", noThink: true },
      { id: "glm-4-flash-250414", noThink: null },
    ],
  },
  deepseek: {
    url: "https://api.deepseek.com/v1/chat/completions",
    keyName: "DEEPSEEK_API_KEY",
    models: [{ id: "deepseek-flash", noThink: null }],
  },
};

const SYSTEM = `你是「每日 AI 新闻」站点的助手。用户会问两类问题，你要先判断再回答。

【第一类：概念与背景】
问某个技术、术语、公司、模型是什么，怎么理解。
→ 用你自己的知识回答，讲准确、讲通俗，必要时打个比方。

【第二类：站内新闻】
问今天/最近发生了什么、某家公司有什么消息。
→ 只能依据用户提供的【今日新闻】回答，不要补充新闻里没有的事实。
→ 新闻里确实没有，就直接说「今天的新闻里没有相关内容」。

判断方法：如果问题指向具体事件，且【今日新闻】里有相关条目，按第二类答；
如果【今日新闻】为空或明显不相关，按第一类答。
两类都沾边时，先答新闻，再补一句背景。

【硬性要求】
- 中文回答，先结论后细节，控制在 200 字以内
- 数字、日期、版本号只在有把握时写，不确定就模糊表述，绝不编造
- 不要用 Markdown 标题和加粗，需要列举时用「1. 2. 3.」
- 不做投资建议，不评价政治人物`;

function jsonReply(origin, statusCode, obj) {
  return {
    statusCode: statusCode,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Access-Control-Allow-Origin": origin || ALLOW_GROUPS[0],
      "Access-Control-Allow-Methods": "POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
      "Access-Control-Max-Age": "86400",
    },
    body: JSON.stringify(obj),
  };
}

/** 用内置 https 发 POST，返回 { status, data }；超时时间由调用方传 */
function httpsPost(urlStr, headers, bodyObj, timeoutMs) {
  return new Promise(function (resolve, reject) {
    var u = new URL(urlStr);
    var payload = JSON.stringify(bodyObj);
    var req = https.request(
      {
        hostname: u.hostname,
        path: u.pathname + (u.search || ""),
        method: "POST",
        headers: Object.assign({}, headers, {
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(payload),
        }),
        timeout: timeoutMs || PER_MODEL_MS,
      },
      function (res) {
        var data = "";
        res.setEncoding("utf8");
        res.on("data", function (c) { data += c; });
        res.on("end", function () {
          try {
            resolve({ status: res.statusCode, data: JSON.parse(data) });
          } catch (e) {
            resolve({ status: res.statusCode, raw: data });
          }
        });
      }
    );
    req.on("error", reject);
    req.on("timeout", function () { req.destroy(new Error("请求超时")); });
    req.write(payload);
    req.end();
  });
}

exports.main_handler = async function (event, context) {
  var method = (event && event.httpMethod) || "POST";
  var origin = pickOrigin(event);

  // 浏览器跨域预检
  if (method === "OPTIONS") {
    return {
      statusCode: 204,
      headers: {
        "Access-Control-Allow-Origin": origin || ALLOW_GROUPS[0],
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Max-Age": "86400",
      },
      body: "",
    };
  }

  if (method !== "POST") {
    return jsonReply(origin, 405, { error: "只接受 POST" });
  }

  // 解析请求体（函数 URL 传来的是字符串）
  var body = (event && event.body) || {};
  if (typeof body === "string") {
    try {
      body = JSON.parse(body);
    } catch (e) {
      return jsonReply(origin, 400, { error: "请求体不是合法 JSON" });
    }
  }

  var question = String(body.question || "").trim().slice(0, 400);
  var contextText = String(body.context || "").slice(0, 8000);
  if (!question) {
    return jsonReply(origin, 400, { error: "问题为空" });
  }

  // 概念题命中缓存就直接返回（同一个实例里，几分钟内重复提问很常见）
  var cacheKey = contextText ? "" : question.toLowerCase();
  if (cacheKey) {
    var hit = CACHE.get(cacheKey);
    if (hit && Date.now() - hit.t < CACHE_TTL) {
      return jsonReply(origin, 200, {
        answer: hit.a,
        usage: hit.u || {},
        provider: hit.p,
        model: hit.m,
        cached: true,
      });
    }
  }

  // 选模型：默认智谱（免费），可用 AI_PROVIDER 切到 deepseek
  var providerName = process.env.AI_PROVIDER || "zhipu";
  var provider = PROVIDERS[providerName];
  if (!provider) {
    return jsonReply(origin, 500, { error: "AI_PROVIDER 只能是 zhipu 或 deepseek" });
  }
  var apiKey = process.env[provider.keyName];
  if (!apiKey) {
    return jsonReply(origin, 500, {
      error: "服务端未配置 " + provider.keyName + "，请在函数配置的环境变量里添加",
    });
  }

  var userMsg = contextText
    ? "【今日新闻】\n" + contextText + "\n\n【问题】\n" + question
    : "【今日新闻】\n（本次没有相关资料）\n\n【问题】\n" + question;

  // 挨个试模型：429（限流）和 5xx 换下一个，其他错误直接返回
  var models = process.env.AI_MODEL
    ? [{ id: process.env.AI_MODEL, noThink: true }]
    : provider.models.slice();
  var lastErr = null;
  var answer = "";
  var usedModel = "";
  var usage = {};
  var t0 = Date.now();

  for (var i = 0; i < models.length; i++) {
    var m = models[i];
    // 剩下的时间不够再等一次就别试了，直接返回已试过的错误
    var left = TOTAL_BUDGET_MS - (Date.now() - t0);
    if (left < 3000) {
      lastErr = lastErr || { model: m.id, reason: "剩余时间不足" };
      break;
    }
    var payload = {
      model: m.id,
      messages: [
        { role: "system", content: SYSTEM },
        { role: "user", content: userMsg },
      ],
      temperature: 0.3,
      max_tokens: 800,
    };
    // 支持关闭思考的模型才传这个参数，传错模型的 API 会直接 400
    if (m.noThink) {
      payload.thinking = { type: "disabled" };
    }

    var resp;
    try {
      resp = await httpsPost(
        provider.url,
        { Authorization: "Bearer " + apiKey },
        payload,
        Math.min(PER_MODEL_MS, left)
      );
    } catch (e) {
      // 超时或网络抖动，记下来换下一个
      lastErr = { model: m.id, reason: String(e.message || e) };
      continue;
    }

    if (resp.status === 200 && resp.data) {
      var d = resp.data;
      answer =
        (d.choices && d.choices[0] && d.choices[0].message && d.choices[0].message.content) || "";
      if (answer.trim()) {
        usedModel = m.id;
        usage = d.usage || {};
        break;
      }
      lastErr = { model: m.id, reason: "模型返回空内容" };
      continue;
    }

    lastErr = {
      model: m.id,
      status: resp.status,
      reason: String(resp.raw || JSON.stringify((resp.data && resp.data.error) || resp.data || {})).slice(0, 160),
    };

    // 429 / 5xx 才换模型；401（key 错）、400（参数错）换模型也没用，直接停
    if (resp.status !== 429 && resp.status < 500) break;
  }

  if (!answer.trim()) {
    return jsonReply(origin, 502, {
      error: "模型暂时不可用，请稍后再试",
      last: lastErr,
      tried: models.map(function (x) { return x.id; }),
    });
  }

  // 写缓存，下次同样的问题直接返回
  if (cacheKey) {
    if (CACHE.size >= CACHE_MAX) {
      // 简单淘汰：删掉最早插入的一条
      var firstKey = CACHE.keys().next().value;
      if (firstKey) CACHE.delete(firstKey);
    }
    CACHE.set(cacheKey, { a: answer.trim(), u: usage, p: providerName, m: usedModel, t: Date.now() });
  }

  return jsonReply(origin, 200, {
    answer: String(answer).trim(),
    usage: usage,
    provider: providerName,
    model: usedModel,
    elapsed_ms: Date.now() - t0,
  });
};
