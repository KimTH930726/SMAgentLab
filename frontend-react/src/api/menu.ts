import { apiFetch } from './client';

/** 메뉴 노출 설정 — 범위(kind:<컬렉션 종류>, 나중엔 role:<역할>)마다 숨길 메뉴 id(10/23). 값은 "숨길 것"만. */
export interface MenuVisibility {
  hidden: Record<string, string[]>;
}

export const getMenuVisibility = () => apiFetch<MenuVisibility>('/admin/menu-visibility');

export const setMenuVisibility = (hidden: Record<string, string[]>) =>
  apiFetch<MenuVisibility>('/admin/menu-visibility', { method: 'PUT', body: JSON.stringify({ hidden }) });
