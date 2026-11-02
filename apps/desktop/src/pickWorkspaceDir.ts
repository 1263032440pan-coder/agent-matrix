/**
 * 弹出系统文件夹选择框，返回绝对路径；取消或非 Tauri 环境返回 null。
 */
export async function pickWorkspaceDirectory(): Promise<string | null> {
  const w = window as unknown as {
    __TAURI__?: { core?: { invoke?: (cmd: string) => Promise<unknown> } };
    __TAURI_INTERNALS__?: { invoke?: (cmd: string) => Promise<unknown> };
  };
  const invoke =
    w.__TAURI__?.core?.invoke ?? w.__TAURI_INTERNALS__?.invoke;
  if (!invoke) {
    return null;
  }
  try {
    const selected = await invoke('pick_directory');
    if (typeof selected === 'string' && selected.trim()) {
      return selected.trim();
    }
    return null;
  } catch {
    return null;
  }
}
