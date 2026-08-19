/**
 * AccountsAdmin — Settings ▸ Accounts.
 *
 * Manage login accounts: create them, enable/disable, and toggle the
 * per-account grantable permissions. Visible only with accounts.manage_staff.
 * The backend enforces the full hierarchy (an admin can only touch staff and
 * only hand out permissions it holds), so a disallowed toggle simply returns
 * 403 and we surface the message.
 */
import { useCallback, useEffect, useState } from "react";
import { toast } from "react-toastify";
import {
  listAccounts,
  listAccessCandidates,
  createAccount,
  updateAccount,
  patchAccountPermissions,
} from "../api/auth";
import { useAuth } from "../hooks/useAuth";

// Permissions an admin may hand to staff (mirrors backend STAFF_GRANTABLE).
// A super_admin can grant more, but these cover the common cases; anything
// the backend rejects is reported as an error.
const GRANTABLE = [
  { key: "streams.view", label: "View camera streams" },
  { key: "history.read", label: "View history" },
  { key: "faces.enroll", label: "Enrol / update faces" },
  { key: "users.write", label: "Edit users" },
  { key: "frontdesk_admin.manage", label: "Manage front-desk config" },
];

const ROLES = ["staff", "admin", "super_admin"];

export default function AccountsAdmin() {
  const { role: myRole, reloadMe, account: me } = useAuth();
  const [accounts, setAccounts] = useState([]);
  const [candidates, setCandidates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ userId: "", username: "", password: "", role: "staff" });

  const canAssignPrivileged = myRole === "super_admin";

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [accountRows, candidateRows] = await Promise.all([
        listAccounts(),
        listAccessCandidates(),
      ]);
      setAccounts(accountRows);
      setCandidates(candidateRows);
    } catch (err) {
      toast.error(err?.message || "Failed to load accounts");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const onCreate = async (e) => {
    e.preventDefault();
    setCreating(true);
    try {
      await createAccount({
        userId: form.userId,
        username: form.username.trim(),
        password: form.password,
        role: form.role,
      });
      toast.success(`Access enabled for “${form.username.trim()}”`);
      setForm({ userId: "", username: "", password: "", role: "staff" });
      await refresh();
    } catch (err) {
      toast.error(err?.message || "Create failed");
    } finally {
      setCreating(false);
    }
  };

  const toggleActive = async (acct) => {
    try {
      await updateAccount(acct.id, { is_active: !acct.is_active });
      await refresh();
    } catch (err) {
      toast.error(err?.message || "Update failed");
    }
  };

  const togglePermission = async (acct, permKey, currentlyOn) => {
    try {
      await patchAccountPermissions(acct.id, {
        grant: currentlyOn ? [] : [permKey],
        revoke: currentlyOn ? [permKey] : [],
      });
      await refresh();
      // If we changed our own permissions, refresh the session view too.
      if (me?.id === acct.id) reloadMe();
    } catch (err) {
      toast.error(err?.message || "Permission change rejected");
    }
  };

  return (
    <div className="bg-card rounded-2xl shadow-md border border-primary/8 p-5">
      <h2 className="text-lg font-bold text-text-main mb-1">Accounts</h2>
      <p className="text-sm text-text-muted mb-5">
        Enable login access for registered employees and doctors, then assign permissions.
      </p>

      {/* Create */}
      <form
        onSubmit={onCreate}
        className="grid grid-cols-1 sm:grid-cols-[1.3fr_1fr_1fr_auto_auto] gap-2.5 mb-6 items-end"
      >
        <label className="flex flex-col gap-1">
          <span className="text-[11px] font-bold text-text-muted uppercase">Registered user</span>
          <select
            value={form.userId}
            onChange={(e) => setForm((f) => ({ ...f, userId: e.target.value }))}
            required
            className="px-3 py-2 rounded-lg bg-background border border-primary/10 text-text-main outline-none focus:border-primary/40"
          >
            <option value="">Select employee or doctor…</option>
            {candidates.map((user) => (
              <option key={user.id} value={user.id}>
                {user.name} · {user.user_type.toLowerCase()}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[11px] font-bold text-text-muted uppercase">Username</span>
          <input
            value={form.username}
            onChange={(e) => setForm((f) => ({ ...f, username: e.target.value }))}
            required
            className="px-3 py-2 rounded-lg bg-background border border-primary/10 text-text-main outline-none focus:border-primary/40"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[11px] font-bold text-text-muted uppercase">Password</span>
          <input
            type="password"
            value={form.password}
            onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))}
            required
            minLength={8}
            className="px-3 py-2 rounded-lg bg-background border border-primary/10 text-text-main outline-none focus:border-primary/40"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[11px] font-bold text-text-muted uppercase">Role</span>
          <select
            value={form.role}
            onChange={(e) => setForm((f) => ({ ...f, role: e.target.value }))}
            className="px-3 py-2 rounded-lg bg-background border border-primary/10 text-text-main outline-none focus:border-primary/40"
          >
            {ROLES.filter((r) => canAssignPrivileged || r === "staff").map((r) => (
              <option key={r} value={r}>
                {r.replace("_", " ")}
              </option>
            ))}
          </select>
        </label>
        <button
          type="submit"
          disabled={creating}
          className="px-4 py-2 rounded-lg bg-primary text-white font-bold shadow-sm hover:bg-primary/90 transition-colors disabled:opacity-60 h-[38px]"
        >
          {creating ? "Adding…" : "Add"}
        </button>
      </form>

      {/* List */}
      {loading ? (
        <div className="py-10 flex justify-center">
          <div className="w-7 h-7 rounded-full border-2 border-primary/20 border-t-primary animate-spin" />
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {accounts.map((a) => {
            const eff = new Set(a.effective_permissions || []);
            const isSelf = me?.id === a.id;
            return (
              <div
                key={a.id}
                className="rounded-xl border border-primary/10 p-4 bg-background/40"
              >
                <div className="flex items-center justify-between gap-3 mb-3">
                  <div className="flex items-center gap-2.5">
                    <span className="font-bold text-text-main">{a.name}</span>
                    <span className="text-xs text-text-muted">@{a.username}</span>
                    <span className="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-primary/10 text-primary capitalize">
                      {a.role.replace("_", " ")}
                    </span>
                    {!a.is_active && (
                      <span className="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-danger/10 text-danger">
                        disabled
                      </span>
                    )}
                    {isSelf && (
                      <span className="text-[11px] font-medium text-text-muted">(you)</span>
                    )}
                  </div>
                  {!isSelf && (
                    <button
                      onClick={() => toggleActive(a)}
                      className={`text-xs font-bold px-3 py-1.5 rounded-lg transition-colors ${
                        a.is_active
                          ? "text-danger hover:bg-danger/10"
                          : "text-success hover:bg-success/10"
                      }`}
                    >
                      {a.is_active ? "Disable" : "Enable"}
                    </button>
                  )}
                </div>

                {/* Permission toggles — only meaningful for non-super_admin
                    targets (super_admins already hold everything). */}
                {a.role !== "super_admin" && (
                  <div className="flex flex-wrap gap-2">
                    {GRANTABLE.map((p) => {
                      const on = eff.has(p.key);
                      const disabled = isSelf; // don't edit your own grants here
                      return (
                        <button
                          key={p.key}
                          disabled={disabled}
                          onClick={() => togglePermission(a, p.key, on)}
                          className={`text-xs font-semibold px-3 py-1.5 rounded-lg border transition-colors ${
                            on
                              ? "bg-primary/10 text-primary border-primary/20"
                              : "bg-transparent text-text-muted border-primary/10 hover:border-primary/30"
                          } ${disabled ? "opacity-50 cursor-not-allowed" : ""}`}
                          title={p.key}
                        >
                          {on ? "✓ " : ""}
                          {p.label}
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
          {accounts.length === 0 && (
            <p className="text-sm text-text-muted py-6 text-center">No accounts yet.</p>
          )}
        </div>
      )}
    </div>
  );
}
