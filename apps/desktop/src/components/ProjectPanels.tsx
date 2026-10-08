import { useEffect, useState } from 'react';
import {
  confirmManualAction,
  createCheckpoint,
  fetchDashboard,
  listCheckpoints,
  rejectManualAction,
  restoreCheckpoint,
} from '../api';

const card: React.CSSProperties = {
  background: '#fff',
  border: '1px solid #eee',
  borderRadius: 8,
  padding: 16,
};

interface ManualAction {
  id: number;
  task_id: number | null;
  action_type: string;
  status: string;
}

interface DeliveryData {
  contract_id: number;
  version: number;
  status: string;
  items: Array<{ id: number; requirement_ref: string; status: string }>;
}

interface CheckpointInfo {
  id: number;
  trigger: string;
  summary: string | null;
  created_at: string | null;
}

/** WP-018: MANUAL 模式待确认队列 + Delivery 契约 + Checkpoint 管理。 */
export function ProjectPanels({
  projectId,
  onChanged,
}: {
  projectId: number;
  onChanged: () => void;
}) {
  const [manualActions, setManualActions] = useState<ManualAction[]>([]);
  const [delivery, setDelivery] = useState<DeliveryData | null>(null);
  const [checkpoints, setCheckpoints] = useState<CheckpointInfo[]>([]);
  const [busy, setBusy] = useState(false);

  const reload = () => {
    fetchDashboard(projectId)
      .then((d) => {
        setManualActions((d as { manual_actions?: ManualAction[] }).manual_actions ?? []);
        setDelivery((d as { delivery?: DeliveryData | null }).delivery ?? null);
      })
      .catch(() => undefined);
    listCheckpoints(projectId)
      .then(setCheckpoints)
      .catch(() => undefined);
  };

  useEffect(reload, [projectId]);

  const onConfirm = async (id: number) => {
    setBusy(true);
    try {
      await confirmManualAction(id);
      reload();
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  const onReject = async (id: number) => {
    setBusy(true);
    try {
      await rejectManualAction(id);
      reload();
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  const onSnapshot = async () => {
    setBusy(true);
    try {
      await createCheckpoint(projectId);
      reload();
    } finally {
      setBusy(false);
    }
  };

  const onRestore = async (id: number) => {
    setBusy(true);
    try {
      await restoreCheckpoint(id);
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ display: 'grid', gap: 16, marginTop: 16 }}>
      <section data-testid="manual-actions" style={card}>
        <h3 style={{ marginTop: 0 }}>Manual Actions（待人工确认）</h3>
        {manualActions.length === 0 && (
          <p style={{ color: '#999' }}>无待确认操作</p>
        )}
        <ul style={{ paddingLeft: 18 }}>
          {manualActions.map((m) => (
            <li key={m.id} style={{ marginBottom: 8 }}>
              #{m.id} {m.action_type} → task {m.task_id}
              <button
                disabled={busy}
                onClick={() => onConfirm(m.id)}
                style={{ marginLeft: 12 }}
              >
                确认执行
              </button>
              <button
                disabled={busy}
                onClick={() => onReject(m.id)}
                style={{ marginLeft: 8 }}
              >
                拒绝
              </button>
            </li>
          ))}
        </ul>
      </section>

      <section data-testid="delivery" style={card}>
        <h3 style={{ marginTop: 0 }}>Delivery Contract</h3>
        {!delivery && <p style={{ color: '#999' }}>暂无交付契约</p>}
        {delivery && (
          <>
            <p>
              契约 v{delivery.version} — {delivery.status}
            </p>
            <ul style={{ paddingLeft: 18 }}>
              {delivery.items.map((i) => (
                <li key={i.id}>
                  {i.requirement_ref} — <strong>{i.status}</strong>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>

      <section data-testid="checkpoints" style={card}>
        <h3 style={{ marginTop: 0 }}>Checkpoints（快照 / 恢复）</h3>
        <button disabled={busy} onClick={onSnapshot}>
          创建快照
        </button>
        {checkpoints.length === 0 && (
          <p style={{ color: '#999' }}>暂无快照</p>
        )}
        <ul style={{ paddingLeft: 18, marginTop: 8 }}>
          {checkpoints.map((c) => (
            <li key={c.id} style={{ marginBottom: 4 }}>
              #{c.id} {c.trigger} — {c.summary ?? ''}
              <button
                disabled={busy}
                onClick={() => onRestore(c.id)}
                style={{ marginLeft: 12 }}
              >
                恢复为新副本
              </button>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
