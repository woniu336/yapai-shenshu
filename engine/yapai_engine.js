/* 牙牌神数 · 占算引擎（JS 版，浏览器 / Node 双用）
 *
 * 与 engine/yapai_engine.py 保持同一口径：
 *   1. 32 张牙牌洗乱一字排开 → 30 个相邻三张滑动窗口
 *   2. 每窗只记最高一开（不同6 > 五子5 > 合巧4 > 分相/马军/对子/么二三/二三靠3 > 正快1）
 *   3. 30 窗相加 = 总开；0 开须重占
 *   4. 连排三次 = 始/中/末
 * 品阶两套口径：calibrated（分位数标定，匹配目标分布） / book（原书 12/10/8/5）
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.YapaiEngine = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const DECK_TILES = [
    ['天牌', 6, 6], ['天牌', 6, 6],
    ['地牌', 1, 1], ['地牌', 1, 1],
    ['人牌', 4, 4], ['人牌', 4, 4],
    ['和牌', 1, 3], ['和牌', 1, 3],
    ['梅花', 5, 5], ['梅花', 5, 5],
    ['长三', 3, 3], ['长三', 3, 3],
    ['板凳', 2, 2], ['板凳', 2, 2],
    ['斧头', 5, 6], ['斧头', 5, 6],
    ['红头十', 4, 6], ['红头十', 4, 6],
    ['高脚七', 1, 6], ['高脚七', 1, 6],
    ['铜锤六', 1, 5], ['铜锤六', 1, 5],
    ['九点', 3, 6], ['九点', 4, 5],
    ['八点', 2, 6], ['八点', 3, 5],
    ['七点', 2, 5], ['七点', 3, 4],
    ['二四', 2, 4],
    ['五点', 2, 3], ['五点', 1, 4],
    ['么二', 1, 2]
  ];

  const DECK = DECK_TILES.map((t, i) => ({ index: i, name: t[0], top: t[1], bottom: t[2], key: t[0] + t[1] + '-' + t[2] }));

  const PATTERN_VALUES = {
    '不同': 6, '五子': 5, '合巧': 4, '分相': 3, '马军': 3,
    '对子': 3, '么二三': 3, '二三靠': 3, '正快': 1, '无开': 0
  };
  const PATTERN_NOTES = {
    '不同': '六面 1~6 各见其一',
    '五子': '六面中五面同点',
    '合巧': '四面同点，余两面之和恰等于该点',
    '分相': '三面同点，另三面亦同点',
    '马军': '双四、双五、双六',
    '对子': '窗内两张完全相同的牌',
    '么二三': '双么、双二、双三',
    '二三靠': '双二、双三、双六',
    '正快': '三面同点，余三面之和 ≥ 14',
    '无开': '不成开数'
  };

  const TIERS_TOP_DOWN = ['上上', '上中', '中平', '中下', '下下'];
  const THRESHOLDS = {
    book: { '上上': 12, '上中': 10, '中平': 8, '中下': 5, '下下': 1 },
    calibrated: { '上上': 21, '上中': 15, '中平': 9, '中下': 3, '下下': 0 }
  };

  /* ---------------- 随机源（可注入种子，便于复现） ---------------- */
  function mulberry32(seed) {
    let a = seed >>> 0;
    return function () {
      a = (a + 0x6D2B79F5) >>> 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  const defaultRng = () => Math.random();

  function shuffleDeck(rand) {
    const randFn = rand || defaultRng;
    const row = DECK.slice();
    for (let i = row.length - 1; i > 0; i--) {
      const j = Math.floor(randFn() * (i + 1));
      const tmp = row[i]; row[i] = row[j]; row[j] = tmp;
    }
    return row;
  }

  /* ---------------- 开数判定 ---------------- */
  function detectWindow(cards) {
    const faces = [];
    for (const c of cards) { faces.push(c.top, c.bottom); }
    const cnt = new Map();
    for (const f of faces) cnt.set(f, (cnt.get(f) || 0) + 1);
    const ordered = faces.slice().sort((x, y) => x - y);
    const counts = Array.from(cnt.values()).sort((x, y) => x - y);
    const eq = (arr, want) => arr.length === want.length && arr.every((v, i) => v === want[i]);

    // 1. 不同 6
    if (eq(ordered, [1, 2, 3, 4, 5, 6])) return { pattern: '不同', value: 6 };
    // 2. 五子 5
    if (eq(counts, [1, 5])) return { pattern: '五子', value: 5 };
    // 3. 合巧 4
    for (const [value, count] of cnt) {
      if (count === 4) {
        const rest = faces.slice();
        for (let i = 0; i < 4; i++) rest.splice(rest.indexOf(value), 1);
        if (rest.reduce((s, v) => s + v, 0) === value) return { pattern: '合巧', value: 4 };
      }
    }
    // 4. 分相
    if (eq(counts, [3, 3]) && cnt.size === 2) return { pattern: '分相', value: 3 };
    // 5. 马军
    if (eq(ordered, [4, 4, 5, 5, 6, 6])) return { pattern: '马军', value: 3 };
    // 6. 对子
    const keys = cards.map(c => c.key || (c.name + c.top + '-' + c.bottom));
    if (keys[0] === keys[1] || keys[1] === keys[2] || keys[0] === keys[2]) return { pattern: '对子', value: 3 };
    // 7. 么二三
    if (eq(ordered, [1, 1, 2, 2, 3, 3])) return { pattern: '么二三', value: 3 };
    // 8. 二三靠
    if (eq(ordered, [2, 2, 3, 3, 6, 6])) return { pattern: '二三靠', value: 3 };
    // 9. 正快 1
    for (const [value, count] of cnt) {
      if (count === 3) {
        const rest = faces.slice();
        for (let i = 0; i < 3; i++) rest.splice(rest.indexOf(value), 1);
        if (rest.reduce((s, v) => s + v, 0) >= 14) return { pattern: '正快', value: 1 };
      }
    }
    return { pattern: '无开', value: 0 };
  }

  /* ---------------- 一次排牌 ---------------- */
  function playRound(rand, row) {
    const cards = row ? row.slice() : shuffleDeck(rand);
    const windows = [];
    const patternCounts = {};
    let total = 0;
    for (let start = 0; start < cards.length - 2; start++) {
      const trio = [cards[start], cards[start + 1], cards[start + 2]];
      const det = detectWindow(trio);
      windows.push({ start, tiles: trio.map(c => c.index), pattern: det.pattern, value: det.value });
      patternCounts[det.pattern] = (patternCounts[det.pattern] || 0) + 1;
      total += det.value;
    }
    return { row: cards, windows, total, patternCounts };
  }

  function grade(total, mode) {
    const th = THRESHOLDS[mode || 'calibrated'];
    for (const tier of TIERS_TOP_DOWN) {
      if (total >= th[tier]) return tier;
    }
    return '下下';
  }

  function oneValidRound(rand, mode) {
    let attempts = 0;
    for (;;) {
      attempts++;
      const rnd = playRound(rand);
      if (rnd.total > 0) return { round: rnd, tier: grade(rnd.total, mode), rerolls: attempts - 1 };
    }
  }

  function drawThree(rand, mode) {
    const rounds = [oneValidRound(rand, mode), oneValidRound(rand, mode), oneValidRound(rand, mode)];
    const tiers = rounds.map(r => r.tier);
    return { mode: mode || 'calibrated', labels: ['始', '中', '末'], tiers, pattern: tiers.join(' '), rounds };
  }

  return {
    DECK, DECK_TILES, PATTERN_VALUES, PATTERN_NOTES, THRESHOLDS, TIERS_TOP_DOWN,
    detectWindow, shuffleDeck, playRound, grade, oneValidRound, drawThree, mulberry32
  };
});
