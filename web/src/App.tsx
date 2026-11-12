import { useCallback, useEffect, useState } from 'react';
import classNames from 'classnames';
import type { SessionMeta, SessionSearchHit } from './api/types';
import {
  ApiRequestError,
  createSession,
  deleteSession,
  listSessions,
  renameSession,
  searchSessions,
} from './api/client';
import type { SessionListItem } from './components/SessionSidebar';
import { SessionSidebar } from './components/SessionSidebar';
import { ChatView } from './chat/ChatView';
import { TraceView } from './trace/TraceView';

// 主区 Tab
type TabKey = 'chat' | 'trace';

// 从 URL hash 中解析当前会话 id
function readSessionIdFromHash(): string | null {
  const matched = window.location.hash.match(/^#\/sessions\/([^/]+)/);
  if (!matched) {
    return null;
  }
  try {
    return decodeURIComponent(matched[1]);
  } catch {
    return matched[1];
  }
}

// 将异常转换为中文错误文案
function toErrorMessage(error: unknown): string {
  if (error instanceof ApiRequestError) {
    return error.message || '请求失败';
  }
  if (error instanceof Error) {
    return error.message || '请求失败';
  }
  return '请求失败';
}

// 清除 URL 中的会话 hash
function clearSessionHash(): void {
  window.history.replaceState(null, '', window.location.pathname + window.location.search);
}

export default function App() {
  const [sessions, setSessions] = useState<SessionMeta[]>([]);
  const [searchResults, setSearchResults] = useState<SessionSearchHit[] | null>(null);
  const [query, setQuery] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(() => readSessionIdFromHash());
  const [activeTab, setActiveTab] = useState<TabKey>('chat');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [collapsed, setCollapsed] = useState(false);

  // 首次加载会话列表
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    void (async () => {
      try {
        const data = await listSessions(50, 0);
        if (!cancelled) {
          setSessions(data.sessions);
        }
      } catch (err) {
        if (!cancelled) {
          setError(toErrorMessage(err));
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // 搜索输入 300ms 防抖调用搜索接口，清空关键词后恢复普通列表
  useEffect(() => {
    const keyword = query.trim();
    if (!keyword) {
      setSearchResults(null);
      setSearchError(null);
      setSearchLoading(false);
      return;
    }
    let cancelled = false;
    setSearchLoading(true);
    const timer = window.setTimeout(() => {
      void (async () => {
        try {
          const data = await searchSessions(keyword);
          if (!cancelled) {
            setSearchResults(data.sessions);
            setSearchError(null);
          }
        } catch (err) {
          if (!cancelled) {
            setSearchResults([]);
            setSearchError(toErrorMessage(err));
          }
        } finally {
          if (!cancelled) {
            setSearchLoading(false);
          }
        }
      })();
    }, 300);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [query]);

  // 监听浏览器前进后退导致的 hash 变化
  useEffect(() => {
    const handleHashChange = (): void => {
      setSelectedId(readSessionIdFromHash());
    };
    window.addEventListener('hashchange', handleHashChange);
    return () => {
      window.removeEventListener('hashchange', handleHashChange);
    };
  }, []);

  // 宽度小于 900px 时自动折叠侧栏
  useEffect(() => {
    const media = window.matchMedia('(max-width: 900px)');
    const handleChange = (event: MediaQueryListEvent): void => {
      setCollapsed(event.matches);
    };
    setCollapsed(media.matches);
    media.addEventListener('change', handleChange);
    return () => {
      media.removeEventListener('change', handleChange);
    };
  }, []);

  // 切换会话并同步 URL hash
  const handleSelect = useCallback((id: string) => {
    setSelectedId(id);
    const nextHash = `#/sessions/${encodeURIComponent(id)}`;
    if (window.location.hash !== nextHash) {
      window.location.hash = nextHash;
    }
  }, []);

  // 新建会话后插入列表并自动选中
  const handleCreate = useCallback(async () => {
    setCreating(true);
    setError(null);
    try {
      const session = await createSession(null);
      setSessions((prev) => [session, ...prev.filter((item) => item.id !== session.id)]);
      setQuery('');
      setSearchResults(null);
      setSelectedId(session.id);
      window.location.hash = `#/sessions/${encodeURIComponent(session.id)}`;
    } catch (err) {
      setError(toErrorMessage(err));
    } finally {
      setCreating(false);
    }
  }, []);

  // 用后端返回的最新元数据同步普通列表与搜索结果
  const applyRename = useCallback((updated: SessionMeta) => {
    setSessions((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
    setSearchResults((prev) =>
      prev
        ? prev.map((item) => (item.id === updated.id ? { ...item, ...updated } : item))
        : null,
    );
  }, []);

  // 重命名：先输入新名称，再二次确认后提交
  const handleRename = useCallback(
    async (session: SessionMeta) => {
      const input = window.prompt('请输入新的会话名称', session.title || '');
      if (input === null) {
        return;
      }
      const title = input.trim();
      if (!title) {
        window.alert('会话名称不能为空');
        return;
      }
      const confirmed = window.confirm(
        `确认将「${session.title || session.id}」重命名为「${title}」？`,
      );
      if (!confirmed) {
        return;
      }
      setError(null);
      try {
        const updated = await renameSession(session.id, title);
        applyRename(updated);
      } catch (err) {
        setError(toErrorMessage(err));
      }
    },
    [applyRename],
  );

  // 删除：二次确认后提交，若删除的是当前会话则清空选中
  const handleDelete = useCallback(
    async (session: SessionMeta) => {
      const confirmed = window.confirm(
        `确认删除会话「${session.title || session.id}」？删除后不可恢复。`,
      );
      if (!confirmed) {
        return;
      }
      setError(null);
      try {
        await deleteSession(session.id);
        setSessions((prev) => prev.filter((item) => item.id !== session.id));
        setSearchResults((prev) =>
          prev ? prev.filter((item) => item.id !== session.id) : null,
        );
        if (selectedId === session.id) {
          setSelectedId(null);
          clearSessionHash();
        }
      } catch (err) {
        setError(toErrorMessage(err));
      }
    },
    [selectedId],
  );

  const items: SessionListItem[] = searchResults ?? sessions;

  return (
    <div className="app">
      <SessionSidebar
        items={items}
        searchMode={searchResults !== null}
        selectedId={selectedId}
        query={query}
        loading={loading}
        error={error}
        searchLoading={searchLoading}
        searchError={searchError}
        creating={creating}
        collapsed={collapsed}
        onToggleCollapsed={() => setCollapsed((prev) => !prev)}
        onQueryChange={setQuery}
        onCreate={handleCreate}
        onSelect={handleSelect}
        onRename={handleRename}
        onDelete={handleDelete}
      />

      <main className="main-area">
        <nav className="tab-bar">
          <button
            type="button"
            className={classNames('tab', activeTab === 'chat' && 'tab--active')}
            onClick={() => setActiveTab('chat')}
          >
            对话
          </button>
          <button
            type="button"
            className={classNames('tab', activeTab === 'trace' && 'tab--active')}
            onClick={() => setActiveTab('trace')}
          >
            轨迹
          </button>
        </nav>
        <section className={classNames('tab-content', activeTab === 'chat' && 'tab-content--chat')}>
          {activeTab === 'chat' ? (
            <ChatView sessionId={selectedId} />
          ) : (
            <TraceView sessionId={selectedId} />
          )}
        </section>
      </main>
    </div>
  );
}
