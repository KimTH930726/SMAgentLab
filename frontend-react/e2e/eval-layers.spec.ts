import { test, expect } from '@playwright/test';
import { groupOrder, mergeLayers } from '../src/utils/evalLayers';

// 브라우저 없이 순수 함수만(10/23 리뷰 L8) — 실데이터엔 list·navigation이 한 파일에 같이 나오지 않아 화면 E2E로는 합치기 회귀를 못 잡는다.
// 성질: 합친 뒤 문항 수 합 = 입력 합(유실·중복 0), 목록 한 줄, 모르는 층은 그대로 뒤에.

test('층 합치기 — list + navigation = 목록 한 줄, 합 보존, 모르는 층 유지', () => {
  const add = (a: { n: number; hit: number }, b: { n: number; hit: number }) => ({ n: a.n + b.n, hit: a.hit + b.hit });
  const byType = { navigation: { n: 7, hit: 5 }, fact: { n: 10, hit: 9 }, list: { n: 3, hit: 3 }, weird: { n: 2, hit: 1 } };
  const rows = mergeLayers(byType, add);
  expect(rows.map((r) => r.name)).toEqual(['사실', '목록', 'weird']);
  expect(rows.find((r) => r.name === '목록')?.v).toEqual({ n: 10, hit: 8 });
  const sum = (xs: { n: number }[]) => xs.reduce((s, x) => s + x.n, 0);
  expect(sum(rows.map((r) => r.v))).toBe(sum(Object.values(byType)));
  expect(mergeLayers({}, add)).toEqual([]);
});

test('판정 표 순서 — 전체가 맨 앞, 그다음 층 순서, 모르는 층은 끝', () => {
  const g = { cross: 1, '전체': 0, weird: 9, fact: 2, abstain: 3 };
  expect(groupOrder(g).map(([k]) => k)).toEqual(['전체', 'fact', 'cross', 'abstain', 'weird']);
});
