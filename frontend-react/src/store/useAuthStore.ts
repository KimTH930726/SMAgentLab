import { create } from 'zustand';
import type { User } from '../types';

interface AuthState {
  user: User | null;
  accessToken: string | null;
  refreshToken: string | null;
  isAuthenticated: boolean;

  login: (user: User, accessToken: string, refreshToken: string) => void;
  logout: () => void;
  updateUser: (user: User) => void;
  setAccessToken: (token: string) => void;
}

const STORAGE_KEY = 'ops_auth';

function loadFromStorage(): Pick<AuthState, 'user' | 'accessToken' | 'refreshToken' | 'isAuthenticated'> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { user: null, accessToken: null, refreshToken: null, isAuthenticated: false };
    const data = JSON.parse(raw);
    return {
      user: data.user ?? null,
      accessToken: data.accessToken ?? null,
      refreshToken: data.refreshToken ?? null,
      isAuthenticated: !!(data.accessToken && data.user),
    };
  } catch {
    return { user: null, accessToken: null, refreshToken: null, isAuthenticated: false };
  }
}

function saveToStorage(user: User | null, accessToken: string | null, refreshToken: string | null) {
  if (user && accessToken) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ user, accessToken, refreshToken }));
  } else {
    localStorage.removeItem(STORAGE_KEY);
  }
}

const initial = loadFromStorage();

export const useAuthStore = create<AuthState>((set) => ({
  ...initial,

  login: (user, accessToken, refreshToken) => {
    saveToStorage(user, accessToken, refreshToken);
    set({ user, accessToken, refreshToken, isAuthenticated: true });
  },

  logout: () => {
    saveToStorage(null, null, null);
    set({ user: null, accessToken: null, refreshToken: null, isAuthenticated: false });
  },

  updateUser: (user) => {
    set((state) => {
      saveToStorage(user, state.accessToken, state.refreshToken);
      return { user };
    });
  },

  setAccessToken: (token) => {
    set((state) => {
      saveToStorage(state.user, token, state.refreshToken);
      return { accessToken: token };
    });
  },
}));
