import rehypeRaw from 'rehype-raw';
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize';
import type { PluggableList } from 'unified';

/**
 * 마크다운 안의 HTML(컨플 표·<br> 등)은 그대로 그리되, 스크립트·이벤트 속성(onerror 등)·javascript: 링크는 지운다(11/03 보안 리뷰 #2 —
 * rehype-raw만 쓰면 지식 본문·답변에 넣은 <img onerror>가 관리자 화면에서 실행돼 토큰을 가져갈 수 있었다).
 * 순서가 중요: raw로 HTML을 트리로 바꾼 "뒤" sanitize. 허용 목록은 GitHub 렌더 기준(defaultSchema) — 코드 블록 언어 class는 그대로 허용된다.
 */
export const safeRehypePlugins: PluggableList = [rehypeRaw, [rehypeSanitize, defaultSchema]];

/** 글자 그대로 보여 주는 곳(검색 카드·근거 카드)용 — 적재가 원문 글자 '#'에 붙인 이스케이프(줄 머리 백슬래시 + #, v2.227)를 뗀다.
 *  마크다운으로 그리는 곳은 렌더러가 알아서 '#'으로 보여 주니 쓰지 않는다. */
export const plainHashes = (text: string) => text.replace(/^([ \t]*)\\#/gm, '$1#');
