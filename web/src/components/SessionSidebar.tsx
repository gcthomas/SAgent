import classNames from 'classnames';
import type {
  SessionMatchSource,
  SessionMeta,
  SessionSearchHit,
} from '../api/types';

// 侧栏列表项：普通列表使用 SessionMeta，搜索结果额外携带命中信息
export type SessionListItem = SessionMeta | SessionSearchHit;

// 搜索命中来源的中文文案
const MATCH_SOURCE_LABELS: Record<SessionMatchSource, string> = {
  title: '标题匹配',
  content: '内容匹配',
  both: '标题与内容匹配',
};

// 判断列表项是否来自搜索结果
function isSearchHit(item: SessionListItem): item is SessionSearchHit {
  return 'match_source' in item;
}

// 格式化 ISO 时间为本地可读时间
function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  const pad = (part: number): string => String(part).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}`;
}

// 会话侧栏属性
export interface SessionSidebarProps {
  items: SessionListItem[];
  searchMode: boolean;
  selectedId: string | null;
  query: string;
  loading: boolean;
  error: string | null;
  searchLoading: boolean;
  searchError: string | null;
  creating: boolean;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  onQueryChange: (value: string) => void;
  onCreate: () => void;
  onSelect: (id: string) => void;
  onRename: (session: SessionMeta) => void;
  onDelete: (session: SessionMeta) => void;
}

// 左侧会话侧栏：新建、搜索、切换、重命名与删除
export function SessionSidebar(props: SessionSidebarProps) {
  const {
    items,
    searchMode,
    selectedId,
    query,
    loading,
    error,
    searchLoading,
    searchError,
    creating,
    collapsed,
    onToggleCollapsed,
    onQueryChange,
    onCreate,
    onSelect,
    onRename,
    onDelete,
  } = props;

  const emptyText = searchMode ? '没有匹配的会话' : '暂无会话';

  return (
    <aside className={classNames('sidebar', collapsed && 'sidebar--collapsed')}>
      <div className="sidebar-header">
        {!collapsed && <span className="sidebar-brand">SAgent</span>}
        <button
          type="button"
          className="icon-button"
          onClick={onToggleCollapsed}
          title={collapsed ? '展开侧栏' : '收起侧栏'}
          aria-label={collapsed ? '展开侧栏' : '收起侧栏'}
        >
          {collapsed ? '»' : '«'}
        </button>
      </div>

      {collapsed ? (
        <div className="sidebar-collapsed-actions">
          <button
            type="button"
            className="icon-button"
            onClick={onCreate}
            disabled={creating}
            title="新建会话"
            aria-label="新建会话"
          >
            +
          </button>
        </div>
      ) : (
        <>
          <div className="sidebar-toolbar">
            <button
              type="button"
              className="primary-button"
              onClick={onCreate}
              disabled={creating}
            >
              {creating ? '创建中...' : '新建会话'}
            </button>
            <input
              className="search-input"
              type="search"
              value={query}
              placeholder="搜索会话标题或内容"
              onChange={(event) => onQueryChange(event.target.value)}
            />
          </div>

          <div className="sidebar-status">
            {searchLoading && <p className="status-text">搜索中...</p>}
            {searchError && <p className="error-text">{searchError}</p>}
            {error && <p className="error-text">{error}</p>}
          </div>

          <div className="session-list-wrapper">
            {loading && <p className="status-text">加载中...</p>}
            {!loading && items.length === 0 && !error && (
              <p className="status-text">{emptyText}</p>
            )}
            <ul className="session-list">
              {items.map((session) => {
                const active = session.id === selectedId;
                const hit = isSearchHit(session);
                return (
                  <li
                    key={session.id}
                    className={classNames('session-item', active && 'session-item--active')}
                  >
                    <button
                      type="button"
                      className="session-select"
                      onClick={() => onSelect(session.id)}
                      title={session.title || session.id}
                    >
                      <span className="session-title">{session.title || session.id}</span>
                      <span className="session-meta">
                        <span className="session-id">{session.id.slice(0, 8)}</span>
                        <span>{formatTime(session.updated_at)}</span>
                        <span>{session.message_count} 条消息</span>
                      </span>
                      {hit && (
                        <span className="session-match">
                          <span className="match-source">
                            {MATCH_SOURCE_LABELS[session.match_source] ?? '匹配'}
                          </span>
                          {session.snippet && (
                            <span className="match-snippet">{session.snippet}</span>
                          )}
                        </span>
                      )}
                    </button>
                    <div className="session-actions">
                      <button
                        type="button"
                        className="text-button"
                        onClick={() => onRename(session)}
                        title="重命名会话"
                      >
                        重命名
                      </button>
                      <button
                        type="button"
                        className="text-button text-button--danger"
                        onClick={() => onDelete(session)}
                        title="删除会话"
                      >
                        删除
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
          </div>
        </>
      )}
    </aside>
  );
}
