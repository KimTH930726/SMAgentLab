import rehypeRaw from 'rehype-raw';
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize';
import type { PluggableList } from 'unified';

/**
 * 마크다운 안의 HTML(컨플 표·<br> 등)은 그대로 그리되, 스크립트·이벤트 속성(onerror 등)·javascript: 링크는 지운다(11/03 보안 리뷰 #2 —
 * rehype-raw만 쓰면 지식 본문·답변에 넣은 <img onerror>가 관리자 화면에서 실행돼 토큰을 가져갈 수 있었다).
 * 순서가 중요: raw로 HTML을 트리로 바꾼 "뒤" sanitize. 허용 목록은 GitHub 렌더 기준(defaultSchema) — 코드 블록 언어 class는 그대로 허용된다.
 */
export const safeRehypePlugins: PluggableList = [rehypeRaw, [rehypeSanitize, defaultSchema]];
