import { apiFetch } from './client';
import type { NamespaceDetail, KnowledgeCategory } from '../types';

// GET /api/namespaces returns string[]
export async function getNamespaces(): Promise<string[]> {
  try {
    return await apiFetch<string[]>('/namespaces');
  } catch (err) {
    console.error('getNamespaces error:', err);
    throw err;
  }
}

export async function getNamespacesDetail(): Promise<NamespaceDetail[]> {
  try {
    return await apiFetch<NamespaceDetail[]>('/namespaces/detail');
  } catch (err) {
    console.error('getNamespacesDetail error:', err);
    throw err;
  }
}

export async function createNamespace(payload: { name: string; description: string }): Promise<void> {
  try {
    return await apiFetch<void>('/namespaces', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  } catch (err) {
    console.error('createNamespace error:', err);
    throw err;
  }
}

export async function renameNamespace(oldName: string, newName: string): Promise<{ name: string }> {
  return apiFetch<{ name: string }>(`/namespaces/${encodeURIComponent(oldName)}`, {
    method: 'PATCH',
    body: JSON.stringify({ new_name: newName }),
  });
}

export async function deleteNamespace(name: string): Promise<void> {
  try {
    await apiFetch<void>(`/namespaces/${encodeURIComponent(name)}`, { method: 'DELETE' });
  } catch (err) {
    console.error('deleteNamespace error:', err);
    throw err;
  }
}

// Category CRUD

export async function getCategories(namespace: string): Promise<KnowledgeCategory[]> {
  return apiFetch<KnowledgeCategory[]>(`/namespaces/${encodeURIComponent(namespace)}/categories`);
}

export async function createCategory(namespace: string, name: string): Promise<KnowledgeCategory> {
  return apiFetch<KnowledgeCategory>(`/namespaces/${encodeURIComponent(namespace)}/categories`, {
    method: 'POST',
    body: JSON.stringify({ name }),
  });
}

export async function renameCategory(namespace: string, catName: string, newName: string): Promise<KnowledgeCategory> {
  return apiFetch<KnowledgeCategory>(`/namespaces/${encodeURIComponent(namespace)}/categories/${encodeURIComponent(catName)}`, {
    method: 'PATCH',
    body: JSON.stringify({ name: newName }),
  });
}

export async function deleteCategory(namespace: string, catName: string): Promise<void> {
  await apiFetch<void>(`/namespaces/${encodeURIComponent(namespace)}/categories/${encodeURIComponent(catName)}`, {
    method: 'DELETE',
  });
}

export async function suggestCategory(namespace: string, content: string): Promise<string | null> {
  const res = await apiFetch<{ suggested_category: string | null }>(
    `/namespaces/${encodeURIComponent(namespace)}/categories/suggest`,
    { method: 'POST', body: JSON.stringify({ content }) },
  );
  return res.suggested_category;
}
