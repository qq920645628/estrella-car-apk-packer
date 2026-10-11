const http = require('http');
const fsp = require('fs/promises');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const KB_DIR = process.env.KB_DIR || '/opt/estrella-kb/data';
const KB_FILE = path.join(KB_DIR, 'kb.json');
const AUDIT_FILE = path.join(KB_DIR, 'audit.log.jsonl');
const BACKUP_DIR = path.join(KB_DIR, 'backups');
const PORT = parseInt(process.env.PORT || '7799', 10);
// 源码分发：仅存储，不编译（出包在用户本地）。路径可被环境变量覆盖
const SRC_FILE = process.env.SRC_FILE || '/opt/estrella-kb/diplay-src/diplay-src.tar.gz';
// 🔒 支持多 Token 轮换（逗号分隔），环境变量覆盖默认值
const TOKENS = (process.env.KB_TOKEN || '').split(',').map(s => s.trim()).filter(Boolean);

// 限频：每 IP 每分钟上限
const RATE_LIMIT = parseInt(process.env.RATE_LIMIT || '120', 10);
const rateBuckets = new Map();

// ---------- 白名单（车型库允许字段） ----------
const SCALAR_FIELDS = ['make','model','year','factory_system_version','android_base_version',
  'arch','chip','dpi','screen','root_status','known_bugs','permission_limits','storage_limits',
  'bt_limits','usb_limits','other_limits','result','source','artifact'];
const ALLOWED_KEYS = new Set([...SCALAR_FIELDS, 'vehicle_key','pitfalls','solutions','variants','logs']);
const PITFALL_KEYS = new Set(['symptom','root_cause','severity','fix_ref','tags','created_at','source']);
const SOLUTION_KEYS = new Set(['problem','steps','stability_rating','risk_level','verified_count','tags','created_at','source']);
const VARIANT_KEYS = new Set(['firmware_batch','hardware_detail_delta','variant_sig','source','created_at']);

const SENSITIVE_KEYS = /(jks|keystore|password|passwd|private|\.pk8|\.p7b|secret|token|api[_-]?key|sign(?:[_-]?pass)?|alias|signature|auth)/i;
const USER_PATH = /(?:[A-Za-z]:[\\/])?(?:Users|home|root)[\\/][^\\/\s:;,]{1,80}/g;
const SECRET_PAIR = /(\b(?:store[_-]?)?password\b|\bpasswd\b|\bsecret\b|\btoken\b|\bapi[_-]?key\b|\bprivate[_-]?key\b|\bsign(?:[_-]?pass)?\b)\s*[:=]\s*['"]?([^\s'"<>]{3,})/gi;
const SECRET_FILE = /\b[\w.\-]{0,60}\.(jks|keystore|pk8|p7b)\b/gi;

// ---------- 车型名归一（F：防止「逸动PLUS / 逸动 plus / 逸动Plus」分裂建库） ----------
// 仅对第 1 段（车型/厂牌）去所有空白并小写；其余段仅 trim+小写，保证年份/系统/安卓版本精确
function normalizeKey(key) {
  if (!key) return key;
  const parts = String(key).split('|');
  return parts.map((p, i) => {
    let s = String(p).trim().toLowerCase();
    if (i === 0) s = s.replace(/\s+/g, ''); // 车型段去全部空白
    return s;
  }).join('|');
}
// 全库主键归一化重映射（load 时一次性调用，幂等）：把历史非规范 key 重命名为规范 key，并同步冲突引用
function normalizeDbKeys(db) {
  const map = {};
  for (const k of Object.keys(db.vehicles)) {
    const nk = normalizeKey(k);
    if (nk && nk !== k) { map[k] = nk; db.vehicles[nk] = db.vehicles[k]; delete db.vehicles[k]; }
  }
  if (Object.keys(map).length) {
    for (const c of (db.conflicts || [])) if (map[c.key]) c.key = map[c.key];
  }
  return db;
}
// 安卓大版本号提取（G：漂移检测），「9.0」「Android 11」「11」均映射为 9 / 11
function majorOf(v) {
  if (v == null) return null;
  const m = String(v).match(/\d+/);
  return m ? parseInt(m[0], 10) : null;
}

function redactStr(s) {
  let out = s.replace(USER_PATH, '<USER_HOME>');
  out = out.replace(SECRET_PAIR, (m, k) => `${k}=<REDACTED>`);
  out = out.replace(SECRET_FILE, '<REDACTED_FILE>');
  if (SENSITIVE_KEYS.test(out) && out.length < 64) return '<REDACTED>';
  return out;
}
function sanitizeValue(v) {
  if (typeof v === 'string') return redactStr(v);
  if (Array.isArray(v)) return v.map(sanitizeValue);
  if (v && typeof v === 'object') { const o = {}; for (const k in v) o[k] = sanitizeValue(v[k]); return o; }
  return v;
}
function cleanList(items, allowed) {
  return (items || []).filter(i => i && typeof i === 'object').map(it => {
    const o = {}; for (const k in it) if (allowed.has(k)) o[k] = sanitizeValue(it[k]); return o;
  });
}
function sanitizeRecord(rec) {
  const out = {};
  for (const k in (rec || {})) {
    if (!ALLOWED_KEYS.has(k)) continue;
    if (SENSITIVE_KEYS.test(k)) continue;
    out[k] = sanitizeValue(rec[k]);
  }
  out.pitfalls = cleanList(out.pitfalls, PITFALL_KEYS);
  out.solutions = cleanList(out.solutions, SOLUTION_KEYS);
  out.variants = cleanList(out.variants, VARIANT_KEYS);
  if ((out.firmware_batch || out.hardware_detail_delta) && !out.variants.length) {
    out.variants = [{ firmware_batch: out.firmware_batch, hardware_detail_delta: out.hardware_detail_delta }];
  }
  out.source = out.source || 'unknown';
  return out;
}

// ================= 进化内核 =================
// 变体签名：同款车型（vehicle_key）若硬件/系统参数不同 -> 视为不同车机变体，禁止死板复用修复
function variantSig(rec) {
  const norm = v => (Array.isArray(v) ? [...v].sort() : (v == null ? '' : String(v)));
  const parts = [
    norm(rec.arch), norm(rec.chip), norm(rec.dpi), norm(rec.root_status),
    norm(rec.android_base_version), norm(rec.factory_system_version),
    norm(rec.storage_limits), norm(rec.permission_limits),
    norm(rec.bt_limits), norm(rec.usb_limits), norm(rec.known_bugs),
  ];
  return crypto.createHash('sha256').update(parts.join('||')).digest('hex').slice(0, 16);
}
// 方案置信度：胜率 * 样本可信度(越小越保守)
function confidenceOf(s) {
  const ok = s.success_count || 0, bad = s.fail_count || 0, n = ok + bad;
  if (n === 0) return 0.5; // 未知样本，中性
  const rate = ok / n;
  const credibility = n / (n + 3); // 样本少则打折
  return Math.round(rate * credibility * 100) / 100;
}
// 相似度（用于无精确匹配时检索“相似车型”）
function similarity(a, b) {
  let score = 0;
  if (a.android_base_version && a.android_base_version === b.android_base_version) score += 0.35;
  if (a.arch && a.arch === b.arch) score += 0.15;
  if (a.chip && b.chip && a.chip === b.chip) score += 0.15;
  const ov = (x, y) => {
    const xs = new Set(x || []), ys = new Set(y || []);
    if (!xs.size && !ys.size) return 0;
    let inter = 0; for (const v of xs) if (ys.has(v)) inter++;
    return (xs.size && ys.size) ? (2 * inter / (xs.size + ys.size)) : 0;
  };
  score += 0.15 * ov(a.storage_limits, b.storage_limits);
  score += 0.10 * ov(a.permission_limits, b.permission_limits);
  score += 0.10 * ov(a.known_bugs, b.known_bugs);
  return Math.round(Math.min(1, score) * 100) / 100;
}

// ---------- 合并语义（与 local_kb.py 一致：禁止覆盖，仅追加缺失项；并带变体区分） ----------
function mergeInto(db, rec) {
  const key = normalizeKey(rec.vehicle_key);
  const sig = variantSig(rec);
  const receipts = [];
  const existing = db.vehicles[key];
  if (!existing) {
    const vehicle = Object.assign({}, rec);
    vehicle.pitfalls = vehicle.pitfalls || [];
    vehicle.solutions = (vehicle.solutions || []).map(seedSolution);
    vehicle.variants = (vehicle.variants && vehicle.variants.length)
      ? vehicle.variants.map(v => Object.assign({ variant_sig: variantSig(Object.assign({}, rec, v)) }, v))
      : [{ variant_sig: sig, firmware_batch: rec.firmware_batch || '默认', hardware_detail_delta: rec.hardware_detail_delta || {} }];
    vehicle.variant_sigs = vehicle.variants.map(v => v.variant_sig);
    vehicle.variant_split = vehicle.variant_sigs.length > 1;
    vehicle.access_count = 1;
    vehicle.created_at = new Date().toISOString();
    vehicle.updated_at = new Date().toISOString();
    db.vehicles[key] = vehicle;
    receipts.push('成功入库：新增车型记录（变体签名 ' + sig + '）');
    return { receipts, conflict: null, variant_split: false };
  }
  existing.access_count = (existing.access_count || 0) + 1;
  existing.updated_at = new Date().toISOString();
  for (const f of SCALAR_FIELDS) {
    if (!existing[f] && rec[f] != null && rec[f] !== '') existing[f] = rec[f];
  }
  // G. 安卓大版本漂移监控：历史基线 vs 本次大版本，跨大版本则告警，绝不覆盖基线
  const baseV = majorOf(existing.android_base_version);
  const incV = majorOf(rec.android_base_version);
  if (baseV != null && incV != null && baseV !== incV) {
    const seen = new Set((existing.android_versions_seen || []).filter(Boolean));
    if (existing.android_base_version) seen.add(existing.android_base_version);
    seen.add(rec.android_base_version);
    existing.android_versions_seen = Array.from(seen);
    existing.android_drift = true;
    receipts.push('⚠ 安卓底层大版本漂移：历史基线 ' + existing.android_base_version +
      ' → 本次 ' + rec.android_base_version + '，旧方案须人工复核后再套用（基线已保留）');
  }
  for (const pf of (rec.pitfalls || [])) {
    if (!existing.pitfalls.some(p => p.symptom === pf.symptom && p.root_cause === pf.root_cause)) {
      pf.created_at = pf.created_at || new Date().toISOString();
      pf.source = pf.source || rec.source || 'unknown';
      existing.pitfalls.push(pf);
      receipts.push('成功入库：新增坑记录');
    }
  }
  let conflict = null;
  for (const sl of (rec.solutions || [])) {
    const ex = existing.solutions.filter(s => s.problem === sl.problem);
    if (!ex.length) {
      existing.solutions.push(seedSolution(sl, rec));
      receipts.push('成功入库：新增方案记录');
    } else if (ex.some(s => JSON.stringify(s.steps) === JSON.stringify(sl.steps))) {
      receipts.push('方案已存在（步骤一致），跳过未重复新增');
    } else {
      conflict = { id: crypto.randomUUID(), key, existing: ex[0], incoming: sl,
        reason: '同问题不同修复步骤，待人工复核', at: new Date().toISOString(), status: 'pending' };
      db.conflicts.push(conflict);
      receipts.push('进入冲突复核队列：方案步骤不一致（禁止覆盖）');
    }
  }
  // 变体拆分：同款车但参数签名不同 -> 新增变体，告警“勿死板复用”
  // 修复：部分字段 upsert（只补缺失字段）不得制造「碎片签名」——
  // 已存在记录时以「历史画像 + 本次提交（本次优先）」合并视角计算签名，保证与完整 params 的 recommend 可对齐
  const SIG_FIELDS = ['arch','chip','dpi','root_status','android_base_version',
    'factory_system_version','storage_limits','permission_limits','bt_limits','usb_limits','known_bugs'];
  let variant_split = false;
  if (!existing.variant_sigs) existing.variant_sigs = (existing.variants || []).map(v => v.variant_sig || variantSig(Object.assign({}, existing, v)));
  const recHasSigField = SIG_FIELDS.some(f => rec[f] != null && rec[f] !== '');
  const mergedView = {};
  for (const f of SIG_FIELDS) mergedView[f] = (rec[f] != null && rec[f] !== '') ? rec[f] : existing[f];
  const effSig = recHasSigField ? variantSig(mergedView) : sig;
  if (!existing.variant_sigs.includes(effSig)) {
    existing.variant_sigs.push(effSig);
    existing.variants = existing.variants || [];
    existing.variants.push({ variant_sig: effSig, firmware_batch: rec.firmware_batch || '未知批次', hardware_detail_delta: rec.hardware_detail_delta || {} });
    existing.variant_split = true;
    variant_split = true;
    receipts.push('⚠ 检出同款车不同车机变体（签名 ' + effSig + '）：已拆分记录，勿对差异车机机械复用修复');
  }
  return { receipts, conflict, variant_split };
}
function seedSolution(sl, rec) {
  return Object.assign({}, sl, {
    success_count: sl.success_count || 0,
    fail_count: sl.fail_count || 0,
    last_tried: null, last_outcome: null,
    confidence: confidenceOf({ success_count: 0, fail_count: 0 }),
    created_at: sl.created_at || new Date().toISOString(),
    source: sl.source || (rec && rec.source) || 'unknown',
  });
}

// 反馈闭环：用户回报成败 -> 更新方案计数；失败自动生成“负向教训”坑
function reportOutcome(db, body) {
  const key = body.vehicle_key;
  const v = db.vehicles[key];
  if (!v) return { status: 'not_found', key };
  const outcome = (body.outcome || '').toLowerCase();
  const ts = new Date().toISOString();
  let touched = false;
  if (body.problem && v.solutions) {
    const s = v.solutions.find(x => x.problem === body.problem && (body.fix_ref ? x.fix_ref === body.fix_ref : true))
           || v.solutions.find(x => x.problem === body.problem);
    if (s) {
      if (outcome === 'success') s.success_count = (s.success_count || 0) + 1;
      else if (outcome === 'fail') s.fail_count = (s.fail_count || 0) + 1;
      s.last_tried = ts; s.last_outcome = outcome;
      s.confidence = confidenceOf(s);
      touched = true;
    }
  }
  // 失败 -> 自动吸取教训：生成/补强负向坑，防止下次机械复用
  if (outcome === 'fail') {
    const symptom = body.symptom || (body.problem ? ('修复「' + body.problem + '」在该车机变体失败') : '未知失败');
    const exist = v.pitfalls.find(p => p.symptom === symptom);
    if (!exist) {
      v.pitfalls.push({
        symptom,
        root_cause: body.root_cause || '该修复在此车机变体未生效/失败，下次须重新评估，勿直接套用历史方案',
        severity: 'HIGH', fix_ref: body.fix_ref || null, tags: ['negative_lesson', 'evolution'],
        created_at: ts, source: 'evolution-auto',
      });
    } else {
      if (!exist.tags) exist.tags = [];
      if (!exist.tags.includes('negative_lesson')) exist.tags.push('negative_lesson');
    }
    touched = true;
  }
  v.updated_at = ts;
  return { status: 'ok', key, outcome, touched, confidence_updated: touched };
}

// 自动判断（进化大脑的推荐接口）
function recommend(db, body) {
  const key = normalizeKey(body.vehicle_key);
  const params = body.params || {};
  const sig = variantSig(params);
  const v = db.vehicles[key];
  if (v) {
    const sigs = (v.variant_sigs || (v.variants || []).map(x => x.variant_sig) || []).filter(Boolean);
    const mismatch = sigs.length > 0 && !sigs.includes(sig);
    const ranked = (v.solutions || []).map(s => ({
      problem: s.problem, steps: s.steps, risk_level: s.risk_level,
      success_count: s.success_count || 0, fail_count: s.fail_count || 0,
      confidence: confidenceOf(s), last_outcome: s.last_outcome,
    })).sort((a, b) => (b.confidence - a.confidence) || ((b.success_count - b.fail_count) - (a.success_count - a.fail_count)));
    const pitfalls = (v.pitfalls || []).map(p => ({ symptom: p.symptom, severity: p.severity, tags: p.tags || [] }));
    // G. 安卓大版本漂移提示（仅当传入 params 含安卓版本且与历史基线跨大版本）
    const driftBase = majorOf(v.android_base_version);
    const driftInc = majorOf(params.android_base_version);
    const android_drift = driftBase != null && driftInc != null && driftBase !== driftInc;
    let warning = mismatch
      ? '同款车但车机参数签名与历史成功变体不一致：历史修复基于此车型的另一车机变体，可能不适用，须先小范围验证再套用'
      : (sigs.length === 0 ? '该车型尚无车机变体基准签名（旧记录），首次写入带完整参数后将建立基准' : null);
    if (android_drift) {
      warning = (warning ? warning + '；' : '') + '⚠ 安卓底层大版本漂移：历史基线 ' + v.android_base_version +
        ' → 本次 ' + params.android_base_version + '，旧方案须人工复核后再套用';
    }
    return {
      matched: 'exact', vehicle_key: key,
      variant_mismatch: mismatch, android_drift, incoming_sig: sig, known_sigs: sigs,
      warning,
      artifact: v.artifact || null,   // 100% 命中出包：客户端凭 artifact 且无 mismatch 判定 ready_apk
      solutions: ranked,
      pitfalls,
      learned_lessons: pitfalls.filter(p => (p.tags || []).includes('negative_lesson')).length,
    };
  }
  // 无精确匹配 -> 相似车型检索（绝不冒充同款）
  const scored = Object.values(db.vehicles).map(o => ({
    vehicle: o, sim: similarity(params, o),
  })).filter(x => x.sim > 0).sort((a, b) => b.sim - a.sim).slice(0, 3);
  return {
    matched: 'similar', vehicle_key: key, incoming_sig: sig,
    warning: 'MCP 无该车型精确记录，以下为相似车型参考（非同款，须重新评估，勿直接套用）',
    similar: scored.map(x => ({
      vehicle_key: x.vehicle.vehicle_key, similarity: x.sim, arch: x.vehicle.arch,
      android_base_version: x.vehicle.android_base_version,
      best_solution: (x.vehicle.solutions || []).slice().sort((a, b) => confidenceOf(b) - confidenceOf(a))[0] || null,
      confidence: (x.vehicle.solutions || []).length ? confidenceOf((x.vehicle.solutions || []).slice().sort((a, b) => confidenceOf(b) - confidenceOf(a))[0]) : 0.5,
    })),
  };
}

// 冲突半自动仲裁（D）：基于双方「证据可信度」给建议；证据不足时强制人工，绝不强裁
function arbitrate(c) {
  const e = c.existing || {}, inc = c.incoming || {};
  const eOk = e.success_count || 0, eBad = e.fail_count || 0;
  const eConf = confidenceOf(e);
  const incStable = inc.stability_rating || 0;
  if (eBad > 0 && eOk === 0) {
    return { suggest: 'apply_incoming', reason: '历史方案已有失败记录且零成功，倾向采用新方案（仍建议人工确认）' };
  }
  if (eConf >= 0.8 && eOk >= 2) {
    return { suggest: 'keep_existing', reason: '历史方案成功样本充足且置信度高，建议保留' };
  }
  if (incStable >= 4 && eOk === 0) {
    return { suggest: 'apply_incoming', reason: '新方案稳定评级高且历史无成功样本，建议采用新方案' };
  }
  return { suggest: 'needs_review', reason: '双方证据均不足，须人工复核（不自动裁决）' };
}

// ---------- 持久化 + 审计 + 备份 ----------
let cache = null;
let writeChain = Promise.resolve();
async function load() {
  if (cache) return cache;
  try {
    const raw = await fsp.readFile(KB_FILE, 'utf8');
    cache = JSON.parse(raw);
  } catch {
    cache = { vehicles: {}, conflicts: [], meta: { created_at: new Date().toISOString() } };
    await persist();
  }
  cache.vehicles = cache.vehicles || {};
  cache.conflicts = cache.conflicts || [];
  cache.meta = cache.meta || {};
  normalizeDbKeys(cache); // F：首次加载即把历史主键归一化，避免分裂
  return cache;
}
async function persist() {
  await fsp.mkdir(KB_DIR, { recursive: true });
  const tmp = KB_FILE + '.tmp';
  await fsp.writeFile(tmp, JSON.stringify(cache, null, 2));
  await fsp.rename(tmp, KB_FILE);
  dailyBackup();
}
function enqueueWrite() {
  writeChain = writeChain.then(persist).catch(e => console.error('persist err', e));
  return writeChain;
}
function auditLog(obj) {
  try { fs.appendFileSync(AUDIT_FILE, JSON.stringify(Object.assign({ at: new Date().toISOString() }, obj)) + '\n'); }
  catch (e) { console.error('audit err', e); }
}
function dailyBackup() {
  try {
    const d = new Date().toISOString().slice(0, 10);
    const f = path.join(BACKUP_DIR, d + '-kb.json');
    if (!fs.existsSync(f)) { fs.mkdirSync(BACKUP_DIR, { recursive: true }); fs.copyFileSync(KB_FILE, f); }
    // 清理 30 天前的备份
    const keep = Date.now() - 30 * 86400 * 1000;
    for (const fn of fs.readdirSync(BACKUP_DIR)) {
      const fp = path.join(BACKUP_DIR, fn);
      try { if (fs.statSync(fp).mtimeMs < keep) fs.unlinkSync(fp); } catch {}
    }
  } catch (e) { console.error('backup err', e); }
}

function send(res, code, obj) {
  res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify(obj));
}
function auth(req) {
  const h = req.headers['authorization'] || '';
  const t = h.startsWith('Bearer ') ? h.slice(7) : '';
  return TOKENS.includes(t);
}
function rateLimited(ip, token) {
  const now = Date.now();
  const key = ip + '|' + (token ? 'tok' : 'anon'); // E：按 IP+Token 维度限频，互不挤占
  const arr = (rateBuckets.get(key) || []).filter(ts => now - ts < 60000);
  arr.push(now);
  rateBuckets.set(key, arr);
  return arr.length > RATE_LIMIT;
}
// E：认证失败异常告警（仅记录 audit，不阻断）—— 防撞库/探测
const authFailBuckets = new Map();
function noteAuthFail(ip) {
  const now = Date.now();
  const arr = (authFailBuckets.get(ip) || []).filter(ts => now - ts < 600000); // 10 分钟窗口
  arr.push(now);
  authFailBuckets.set(ip, arr);
  if (arr.length === 30) {
    auditLog({ op: 'auth_failure_burst', ip, count: arr.length, note: '疑似撞库/令牌探测，请核查来源' });
  }
}
function readBody(req) {
  return new Promise((resolve, reject) => {
    let d = '';
    req.on('data', c => { d += c; if (d.length > 1e7) req.destroy(); });
    req.on('end', () => { try { resolve(d ? JSON.parse(d) : {}); } catch (e) { reject(e); } });
    req.on('error', reject);
  });
}

// ============================================================
// 第 0 步「环境与源码体检」——规则在服务端维护，客户端拉取后本地执行。
// 这样以后要改检测规则（换 JDK 版本要求、加候选路径、加组件），只改这里即可热生效，
// 无需重新分发/更新客户端 Skill 包。检测动作本身必须在本地做：服务端扫不到用户磁盘。
// ============================================================
const PREFLIGHT_SPEC = {
  spec_version: 3,
  updated_at: '2026-10-11',
  note: '第 0 步体检规则。客户端 scripts/preflight.py 拉取后按此规则在本地执行；规则变更无需更新 Skill。',

  project: {
    // 占位符 {home} {desktop} 由客户端展开
    candidates: [
      { label: '约定·中文源码根', path: '{desktop}/123/DiPlay-Universal' },
      { label: '约定·ASCII 构建副本', path: '{home}/DiPlayBuild/DiPlay-Universal' },
      { label: '环境变量', path: '{env:DIPLAY_PROJECT}' },
    ],
    fingerprints: ['settings.gradle.kts', 'settings.gradle'],
    modules: ['automotive', 'retro', 'legacy', 'mid81', 'common', 'shared'],
    fingerprint_text: 'com.shilapi',
    require_ascii: true,
    ascii_copy_to: '{home}/DiPlayBuild/DiPlay-Universal',
    deep_scan_roots: ['{desktop}', '{home}', '{home}/DiPlayBuild'],
    deep_scan_depth: 3,
  },

  modules: {
    retro: { api: '4.2.2~4.4', minSdk: 17 },
    legacy: { api: '6.0~8.0', minSdk: 23 },
    mid81: { api: '8.1', minSdk: 23 },
    automotive: { api: '9.0+', minSdk: 28 },
  },
  flavors: ['universal', 'lite', 'cluster', 'hd', 'ble'],

  jdk: {
    min_major: 17, max_major: 24, recommend: '17',
    reason: 'AGP9 / Gradle 9 要求 JDK 17+',
    search_win: [
      '{home}/.workbuddy/binaries/jdk',
      'C:/Program Files/Java',
      'C:/Program Files/Eclipse Adoptium',
      'C:/Program Files/Android/Android Studio/jbr',
      'C:/Program Files/Microsoft',
    ],
    search_nix: ['/usr/lib/jvm', '/opt/java', '/Library/Java/JavaVirtualMachines'],
    install: 'winget install Microsoft.OpenJDK.17  或  https://adoptium.net/temurin/releases/?version=17',
  },

  sdk: {
    compile_sdk: 37,
    build_tools: '37.0.0',
    ndk_required_for: ['shared', 'legacy', 'mid81'],
    ndk_prefer: ['29.0.14206865', '28.0.13004108'],
    roots: ['{env:ANDROID_HOME}', '{env:ANDROID_SDK_ROOT}', '{home}/AppData/Local/Android/Sdk', 'C:/Android/Sdk'],
    install: {
      platform: 'sdkmanager --install "platforms;android-37"',
      build_tools: 'sdkmanager --install "build-tools;37.0.0"',
      ndk: 'sdkmanager --install "ndk;29.0.14206865"',
      cmdline_tools: 'Android Studio → SDK Manager → SDK Tools → Android SDK Command-line Tools',
    },
  },

  gradle: { wrapper_note: '首次构建需联网下载 wrapper 分发包，或预置 ~/.gradle/wrapper/dists 缓存' },
  signing: { keystore: 'release/diplay-release.jks', props: 'release/keystore.properties' },

  // 已知环境坑（命中即给建议，避免重复踩）
  env_pitfalls: [
    { id: 'E1', when: 'project_path_has_non_ascii', advice: 'AGP 拒绝非 ASCII 工程路径，必须 robocopy 到 ASCII 副本再构建' },
    { id: 'E2', when: 'jdk_missing_or_lt17', advice: '装 JDK 17 并设 JAVA_HOME；AGP9 不支持 JDK 11 及以下' },
    { id: 'E3', when: 'platform_missing', advice: 'compileSdk 对应 platform 未装，gradle 同步必失败' },
    { id: 'E4', when: 'build_tools_missing', advice: '缺 aapt2/zipalign/apksigner，产物无法校验' },
    { id: 'E5', when: 'ndk_missing', advice: '含 native 的模块会失败，装 NDK 28/29' },
    { id: 'E6', when: 'gradlew_missing', advice: '工程不完整，重新拷贝完整工程' },
    { id: 'E7', when: 'keystore_missing', advice: '只能出 debug 包，车机覆盖升级会被拒' },
  ],
};
function preflightSpec() {
  return Object.assign({}, PREFLIGHT_SPEC, { served_at: new Date().toISOString() });
}

// 匿名环境画像：只收「有没有 / 版本号 / 缺什么」，不收绝对路径与个人信息
function recordEnvProfile(db, body) {
  db.env_profiles = db.env_profiles || { total: 0, by_os: {}, jdk: {}, missing_platform: {}, missing_component: {}, blocking: {}, ready: 0, not_ready: 0, last_at: null };
  const E = db.env_profiles;
  const b = body && typeof body === 'object' ? body : {};
  const s = (v, max) => String(v == null ? '' : v).slice(0, max || 40);

  E.total++;
  E.by_os[s(b.os, 20) || 'unknown'] = (E.by_os[s(b.os, 20) || 'unknown'] || 0) + 1;
  if (b.jdk_major) E.jdk[String(b.jdk_major)] = (E.jdk[String(b.jdk_major)] || 0) + 1;
  (b.missing_platforms || []).slice(0, 10).forEach(x => {
    E.missing_platform[s(x, 20)] = (E.missing_platform[s(x, 20)] || 0) + 1;
  });
  (b.missing_components || []).slice(0, 10).forEach(x => {
    E.missing_component[s(x, 30)] = (E.missing_component[s(x, 30)] || 0) + 1;
  });
  (b.blocking || []).slice(0, 20).forEach(x => {
    const k = s(x && (x.item || x), 60);
    if (k) E.blocking[k] = (E.blocking[k] || 0) + 1;
  });
  if (b.ready) E.ready++; else E.not_ready++;
  E.last_at = new Date().toISOString();

  // 回执：给客户端即时建议（不依赖历史数据也成立）
  const advice = [];
  if (b.jdk_major && Number(b.jdk_major) < PREFLIGHT_SPEC.jdk.min_major)
    advice.push(PREFLIGHT_SPEC.jdk.reason + '，当前 ' + b.jdk_major + '；' + PREFLIGHT_SPEC.jdk.install);
  if ((b.missing_platforms || []).length)
    advice.push('缺 SDK platform: ' + b.missing_platforms.join(',') + ' → ' + PREFLIGHT_SPEC.sdk.install.platform);
  if ((b.missing_components || []).includes('build-tools'))
    advice.push(PREFLIGHT_SPEC.sdk.install.build_tools);
  if (!b.project_found)
    advice.push('未定位到源码工程；可放到 ' + PREFLIGHT_SPEC.project.ascii_copy_to + ' 或用 --project 指定');

  // 群体信号：同类缺失超过 3 次，说明是普遍问题，值得写进坑库
  const topBlocking = Object.entries(E.blocking).sort((a, c) => c[1] - a[1]).slice(0, 3);
  if (topBlocking.length && topBlocking[0][1] >= 3)
    advice.push('群体信号：已有 ' + topBlocking[0][1] + ' 次同类阻断「' + topBlocking[0][0] + '」，建议沉淀为坑库条目');

  auditLog({ op: 'preflight_report', ready: !!b.ready, os: s(b.os, 20), jdk_major: b.jdk_major || null });
  return { ok: true, advice, aggregate: { total: E.total, ready: E.ready, not_ready: E.not_ready, top_blocking: topBlocking } };
}

const server = http.createServer(async (req, res) => {
  let url;
  try { url = new URL(req.url, 'http://x'); } catch { return send(res, 400, { error: 'bad url' }); }
  const p = url.pathname;
  try {
    if (p === '/health') return send(res, 200, { ok: true, ts: Date.now(), evolution: true });
    const ip = req.socket.remoteAddress || '?';
    const authH = (req.headers['authorization'] || '');
    const tok = authH.startsWith('Bearer ') ? authH.slice(7) : '';
    if (!auth(req)) { noteAuthFail(ip); return send(res, 401, { error: 'unauthorized' }); }
    if (rateLimited(ip, tok)) return send(res, 429, { error: 'rate limited', retry_after: 60 });
    const db = await load();

    if (p === '/kb/query' && req.method === 'GET') {
      const key = normalizeKey(url.searchParams.get('key') || '');
      const rec = db.vehicles[key];
      if (rec) {
        rec.access_count = (rec.access_count || 0) + 1;
        rec.updated_at = new Date().toISOString();
        enqueueWrite();
        // vehicle 与 record 同体：record 为旧字段名（兼容），vehicle 供客户端 100% 命中出包判定
        return send(res, 200, { found: true, record: rec, vehicle: rec, source_tag: 'MCP知识库（线上）' });
      }
      return send(res, 200, { found: false, note: 'MCP暂无该车型变体记录', key });
    }

    if (p === '/kb/upsert' && req.method === 'POST') {
      const body = await readBody(req);
      body.vehicle_key = normalizeKey(body.vehicle_key);
      const key = body.vehicle_key;
      if (!key) return send(res, 400, { error: 'vehicle_key required' });
      const clean = sanitizeRecord(body); // 服务端二次清洗（值级脱敏 + 白名单）
      const { receipts, conflict, variant_split } = mergeInto(db, clean);
      auditLog({ op: 'upsert', key, variant_split, receipts });
      await enqueueWrite();
      return send(res, 200, {
        status: conflict ? 'conflict' : 'ok',
        key, receipts,
        variant_split: !!variant_split,
        conflict_id: conflict ? conflict.id : null,
        note: conflict ? '已进入冲突复核队列（禁止覆盖）' : (variant_split ? '同款车不同车机，已拆分变体' : '已合并/新增'),
      });
    }

    if (p === '/kb/report_outcome' && req.method === 'POST') {
      const body = await readBody(req);
      body.vehicle_key = normalizeKey(body.vehicle_key);
      if (!body.vehicle_key) return send(res, 400, { error: 'vehicle_key required' });
      const r = reportOutcome(db, body);
      auditLog({ op: 'report_outcome', key: body.vehicle_key, outcome: body.outcome, touched: r.touched });
      await enqueueWrite();
      return send(res, 200, r);
    }

    if (p === '/kb/recommend' && req.method === 'POST') {
      const body = await readBody(req);
      if (!body.vehicle_key) return send(res, 400, { error: 'vehicle_key required' });
      const r = recommend(db, body);
      return send(res, 200, Object.assign({ source_tag: 'MCP知识库（线上·进化推荐）' }, r));
    }

    if (p === '/kb/stats' && req.method === 'GET') {
      const by_result = { success: 0, fail: 0, partial: 0, unknown: 0 };
      let total_access = 0, pitfalls = 0, solutions = 0, learned = 0, variant_splits = 0, outcomes = 0;
      Object.values(db.vehicles).forEach(v => {
        const r = (v.result || 'unknown').toLowerCase();
        by_result[r] = (by_result[r] || 0) + 1;
        total_access += (v.access_count || 0);
        pitfalls += (v.pitfalls || []).length;
        (v.pitfalls || []).forEach(p => { if ((p.tags || []).includes('negative_lesson')) learned++; });
        (v.solutions || []).forEach(s => { solutions++; outcomes += (s.success_count || 0) + (s.fail_count || 0); });
        if (v.variant_split) variant_splits++;
      });
      const autoResolvable = (db.conflicts || []).filter(c => arbitrate(c).suggest !== 'needs_review').length;
      return send(res, 200, {
        total: Object.keys(db.vehicles).length,
        by_result, total_access,
        pitfalls, solutions,
        learned_lessons: learned,
        variant_splits,
        total_outcomes: outcomes,
        conflicts: db.conflicts.length,
        auto_resolvable_conflicts: autoResolvable,
        evolution: true,
        // 环境画像（第 0 步体检汇总，匿名）
        env_profiles: (db.env_profiles && db.env_profiles.total) || 0,
        env_ready: (db.env_profiles && db.env_profiles.ready) || 0,
        env_top_blocking: Object.entries((db.env_profiles && db.env_profiles.blocking) || {})
          .sort((a, b) => b[1] - a[1]).slice(0, 3).map(([k, v]) => ({ item: k, count: v })),
        preflight_spec_version: PREFLIGHT_SPEC.spec_version,
      });
    }

    if (p === '/kb/export' && req.method === 'GET') {
      const fmt = url.searchParams.get('format') || 'json';
      if (fmt === 'csv') {
        const rows = Object.values(db.vehicles).map(v =>
          [v.vehicle_key, v.make, v.year, v.android_base_version, v.result || '', (v.solutions || []).length].join(','));
        return send(res, 200, { csv: ['key,make,year,android,result,solutions', ...rows].join('\n') });
      }
      return send(res, 200, db);
    }

    if (p === '/kb/conflicts' && req.method === 'GET')
      return send(res, 200, { conflicts: (db.conflicts || []).map(c => Object.assign({}, c, arbitrate(c))) });
    if (p === '/kb/list' && req.method === 'GET') return send(res, 200, { keys: Object.keys(db.vehicles) });

    if (p === '/kb/resolve' && req.method === 'POST') {
      const body = await readBody(req);
      const c = db.conflicts.find(x => x.id === body.conflict_id);
      if (!c) return send(res, 404, { error: 'conflict not found' });
      let action = body.action;
      if (action === 'auto') {            // D：半自动仲裁——按证据建议一键裁决；证据不足则拒绝并提示人工
        const a = arbitrate(c);
        if (a.suggest === 'needs_review') return send(res, 400, { error: '该冲突证据不足，仍需人工复核', suggest: a });
        action = a.suggest;               // apply_incoming | keep_existing
        body._auto_reason = a.reason;
      }
      if (action === 'apply_incoming') {
        // incoming 是一条 solution（problem/steps/...），必须替换 vehicle.solutions 中对应问题的方案，
        // 而不是合并进 vehicle 顶层（否则既污染记录又不生效）
        const veh = db.vehicles[c.key];
        if (veh) {
          if (!Array.isArray(veh.solutions)) veh.solutions = [];
          const idx = veh.solutions.findIndex(s => s.problem === c.incoming.problem);
          const seeded = seedSolution(c.incoming, veh);
          if (idx >= 0) veh.solutions[idx] = seeded;
          else veh.solutions.push(seeded);
          veh.updated_at = new Date().toISOString();
        }
      } else if (action !== 'keep_existing') {
        return send(res, 400, { error: 'action must be apply_incoming|keep_existing|auto' });
      }
      c.status = 'resolved:' + action + (body._auto_reason ? ' (auto)' : '');
      auditLog({ op: 'resolve', conflict_id: body.conflict_id, action, auto: !!body._auto_reason, reason: body._auto_reason || null });
      await enqueueWrite();
      return send(res, 200, { status: 'resolved', conflict_id: body.conflict_id, action, auto: !!body._auto_reason, reason: body._auto_reason || null });
    }

    // ---------- 第 0 步体检：规则云端下发（改规则即热更新，客户端 Skill 不必再改） ----------
    if (p === '/kb/preflight_spec' && req.method === 'GET') {
      return send(res, 200, preflightSpec());
    }

    // 客户端体检结果（匿名）回传：聚合「环境画像」，发现普遍性缺失与坏组合
    if (p === '/kb/preflight_report' && req.method === 'POST') {
      const body = await readBody(req);
      const r = recordEnvProfile(db, body);
      await enqueueWrite();
      return send(res, 200, r);
    }

    // ---------- 源码分发：仅存储，不编译（出包在用户本地） ----------
    // 元数据：文件名/size/sha256，供客户端下载后校验完整性
    if (p === '/kb/source/meta' && req.method === 'GET') {
      try {
        const st = await fsp.stat(SRC_FILE);
        const buf = await fsp.readFile(SRC_FILE);
        const sha = crypto.createHash('sha256').update(buf).digest('hex');
        return send(res, 200, {
          name: 'diplay-src.tar.gz',
          size: st.size,
          sha256: sha,
          note: 'DiPlay-Universal 源码快照（已排除 build/.gradle/签名密钥，含 MFi 凭证）。出包请在本地完成。',
        });
      } catch (e) {
        return send(res, 404, { error: 'source not available', detail: String(e) });
      }
    }
    // 流式下载源码 tarball（token 鉴权，与全站一致）
    if (p === '/kb/source' && req.method === 'GET') {
      try {
        const st = await fsp.stat(SRC_FILE);
        res.writeHead(200, {
          'Content-Type': 'application/gzip',
          'Content-Length': st.size,
          'Content-Disposition': 'attachment; filename="diplay-src.tar.gz"',
        });
        const rs = fs.createReadStream(SRC_FILE);
        rs.pipe(res);
        rs.on('error', () => { try { res.destroy(); } catch (e) {} });
        return;
      } catch (e) {
        return send(res, 404, { error: 'source not available', detail: String(e) });
      }
    }

    return send(res, 404, { error: 'not found' });
  } catch (e) {
    console.error('ERR', e);
    return send(res, 500, { error: String(e) });
  }
});

server.listen(PORT, '0.0.0.0', () => console.log(`Estrella KB MCP listening on :${PORT} (tokens=${TOKENS.length}, evolution=on)`));
