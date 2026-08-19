import React, { useState, useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "react-toastify";
import { useWebcams } from "../hooks/useWebcams";
import CameraSelect from "../components/CameraSelect";
import CustomSelect from "../components/CustomSelect";
import { API_URL } from "../config";

const MAX_SAMPLES = 10;

const USER_TYPES = [
  { value: "PATIENT", label: "Patient" },
  { value: "DOCTOR", label: "Doctor" },
  { value: "EMPLOYEE", label: "Employee" },
  { value: "VISITOR", label: "Visitor" },
  { value: "RELATIVE", label: "Relative" },
];

const RELATION_TYPES = [
  { value: "SPOUSE", label: "Spouse" },
  { value: "PARENT", label: "Parent" },
  { value: "CHILD", label: "Child" },
  { value: "SIBLING", label: "Sibling" },
  { value: "GUARDIAN", label: "Guardian" },
  { value: "OTHER", label: "Other" },
];

const TYPE_BADGE = {
  PATIENT:  { bg: "bg-blue-100 dark:bg-blue-500/15",       text: "text-blue-700 dark:text-blue-300",       label: "Patient" },
  DOCTOR:   { bg: "bg-emerald-100 dark:bg-emerald-500/15", text: "text-emerald-700 dark:text-emerald-300", label: "Doctor" },
  EMPLOYEE: { bg: "bg-amber-100 dark:bg-amber-500/15",     text: "text-amber-700 dark:text-amber-300",     label: "Employee" },
  VISITOR:  { bg: "bg-purple-100 dark:bg-purple-500/15",   text: "text-purple-700 dark:text-purple-300",   label: "Visitor" },
  RELATIVE: { bg: "bg-pink-100 dark:bg-pink-500/15",       text: "text-pink-700 dark:text-pink-300",       label: "Relative" },
};

const TYPE_LABEL = (t) => TYPE_BADGE[t]?.label || "Patient";

// ── Skeleton Row ──
function SkeletonRow() {
  return (
    <div className="p-5 sm:px-6 flex items-center gap-5 animate-pulse">
      <div className="h-14 w-14 rounded-xl skeleton shrink-0" />
      <div className="flex flex-col gap-2.5 flex-1">
        <div className="h-5 w-40 rounded-lg skeleton" />
        <div className="h-3.5 w-24 rounded-lg skeleton" />
      </div>
      <div className="flex gap-2.5">
        <div className="h-10 w-24 rounded-xl skeleton" />
        <div className="h-10 w-28 rounded-xl skeleton" />
        <div className="h-10 w-20 rounded-xl skeleton" />
      </div>
    </div>
  );
}

export default function Patients() {
  const navigate = useNavigate();
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [typeFilter, setTypeFilter] = useState(null); // null = ALL
  const [search, setSearch] = useState("");

  const [editingUser, setEditingUser] = useState(null);
  const [editForm, setEditForm] = useState({});

  // Relations panel (inside edit modal)
  const [relations, setRelations] = useState([]);
  const [relationLoading, setRelationLoading] = useState(false);
  const [relationQuery, setRelationQuery] = useState("");
  const [relationResults, setRelationResults] = useState([]);
  const [relationSearching, setRelationSearching] = useState(false);
  const [relationPickType, setRelationPickType] = useState("SPOUSE");
  const [relationAdding, setRelationAdding] = useState(false);

  const EMPTY_EDIT = {
    name: "",
    user_type: "PATIENT",
    age: "",
    gender: "",
    dob: "",
    contact_number: "",
    address: "",
    department: "",
    doctor: "",
    category: "",
    role: "",
    specialty: "",
    staff_department: "",
    opd_department_id: "",
    opd_room_id: "",
    purpose: "",
    note: "",
  };

  const updateEditField = (key, value) =>
    setEditForm((prev) => ({ ...prev, [key]: value }));

  const [updatingFace, setUpdatingFace] = useState(null);

  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const [isCameraOn, setIsCameraOn] = useState(false);
  const [isProcessingFace, setIsProcessingFace] = useState(false);

  const { devices, selectedDeviceId, setSelectedDeviceId, refreshDevices } =
    useWebcams();
  const [samples, setSamples] = useState([]);

  // ── Avatar color from name ──
  const getAvatarColors = (name) => {
    const charCode = name.charCodeAt(0);
    const hue = (charCode * 37) % 360;
    return {
      bg: `hsl(${hue}, 70%, 92%)`,
      text: `hsl(${hue}, 55%, 38%)`,
      border: `hsl(${hue}, 55%, 82%)`,
    };
  };

  const fetchUsers = async () => {
    try {
      setLoading(true);
      const qs = new URLSearchParams();
      if (typeFilter) qs.set("type", typeFilter);
      if (search.trim()) qs.set("q", search.trim());
      const url = `${API_URL}/users${qs.toString() ? `?${qs}` : ""}`;
      const res = await fetch(url, {
        headers: { "ngrok-skip-browser-warning": "true" },
      });
      if (!res.ok) throw new Error("Failed to fetch users");
      const data = await res.json();
      setUsers(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  // Re-fetch on filter change; debounce search input.
  useEffect(() => {
    const t = setTimeout(fetchUsers, search ? 250 : 0);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [typeFilter, search]);

  // --- Deletion ---
  const handleDelete = async (user) => {
    const typeLabel = TYPE_LABEL(user.user_type).toLowerCase();
    if (!window.confirm(`Delete this ${typeLabel} permanently?`)) return;
    try {
      const res = await fetch(`${API_URL}/users/${user.id}`, {
        method: "DELETE",
        headers: { "ngrok-skip-browser-warning": "true" },
      });
      if (!res.ok) throw new Error("Failed to delete user");
      setUsers((prev) => prev.filter((u) => u.id !== user.id));
      toast.success(`${TYPE_LABEL(user.user_type)} deleted successfully`);
    } catch (err) {
      toast.error("Delete failed: " + err.message);
    }
  };

  // --- Edit (all fields, type-aware) ---
  const openEditModal = (user) => {
    setEditingUser(user);
    setEditForm({
      name: user.name || "",
      user_type: user.user_type || "PATIENT",
      age: user.age != null ? String(user.age) : "",
      gender: user.gender || "",
      dob: user.dob ? user.dob.slice(0, 10) : "",
      contact_number: user.contact_number || "",
      address: user.address || "",
      department: user.department || "",
      doctor: user.doctor || "",
      category: user.category || "",
      role: user.role || "",
      specialty: user.specialty || "",
      staff_department: user.staff_department || "",
      opd_department_id: user.opd_department_id != null ? String(user.opd_department_id) : "",
      opd_room_id: user.opd_room_id != null ? String(user.opd_room_id) : "",
      purpose: user.purpose || "",
      note: user.note || "",
    });
    fetchRelations(user.id);
  };

  const fetchRelations = async (userId) => {
    setRelationLoading(true);
    try {
      const res = await fetch(`${API_URL}/users/${userId}/relations`, {
        headers: { "ngrok-skip-browser-warning": "true" },
      });
      if (!res.ok) throw new Error("Failed to fetch relations");
      setRelations(await res.json());
    } catch {
      setRelations([]);
    } finally {
      setRelationLoading(false);
    }
  };

  // Reset relations + picker state when the edit modal closes.
  useEffect(() => {
    if (!editingUser) {
      setRelations([]);
      setRelationQuery("");
      setRelationResults([]);
      setRelationPickType("SPOUSE");
    }
  }, [editingUser]);

  // Debounced search for relation picker.
  useEffect(() => {
    if (!editingUser) return;
    if (relationQuery.trim().length < 2) {
      setRelationResults([]);
      return;
    }
    const t = setTimeout(async () => {
      setRelationSearching(true);
      try {
        const res = await fetch(
          `${API_URL}/users?q=${encodeURIComponent(relationQuery.trim())}`,
          { headers: { "ngrok-skip-browser-warning": "true" } },
        );
        const data = res.ok ? await res.json() : [];
        setRelationResults(data.filter((u) => u.id !== editingUser.id).slice(0, 8));
      } catch {
        setRelationResults([]);
      } finally {
        setRelationSearching(false);
      }
    }, 250);
    return () => clearTimeout(t);
  }, [relationQuery, editingUser]);

  const addRelation = async (partner) => {
    if (!editingUser) return;
    setRelationAdding(true);
    try {
      const res = await fetch(`${API_URL}/users/${editingUser.id}/relations`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "ngrok-skip-browser-warning": "true",
        },
        body: JSON.stringify({
          related_user_id: partner.id,
          relation_type: relationPickType,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "Failed to link");
      }
      toast.success(`Linked ${partner.name}`);
      setRelationQuery("");
      setRelationResults([]);
      fetchRelations(editingUser.id);
    } catch (err) {
      toast.error("Link failed: " + err.message);
    } finally {
      setRelationAdding(false);
    }
  };

  const removeRelation = async (relationId) => {
    if (!editingUser) return;
    try {
      const res = await fetch(
        `${API_URL}/users/${editingUser.id}/relations/${relationId}`,
        { method: "DELETE", headers: { "ngrok-skip-browser-warning": "true" } },
      );
      if (!res.ok && res.status !== 204) throw new Error("Failed to unlink");
      fetchRelations(editingUser.id);
    } catch (err) {
      toast.error("Unlink failed: " + err.message);
    }
  };

  const submitEditUser = async () => {
    if (!editForm.name.trim()) return;
    const numericKeys = new Set(["age", "opd_department_id", "opd_room_id"]);
    const payload = {};
    Object.entries(editForm).forEach(([k, v]) => {
      if (v !== "" && v !== null && v !== undefined) {
        payload[k] = numericKeys.has(k) ? Number(v) : v;
      }
    });
    try {
      const res = await fetch(`${API_URL}/users/${editingUser.id}`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          "ngrok-skip-browser-warning": "true",
        },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "Failed to update user");
      }
      const updatedUser = await res.json();
      setUsers((prev) =>
        prev.map((u) => (u.id === updatedUser.id ? updatedUser : u)),
      );
      setEditingUser(null);
      toast.success(`${TYPE_LABEL(updatedUser.user_type)} updated`);
    } catch (err) {
      toast.error("Update failed: " + err.message);
    }
  };

  // --- Camera Logic ---
  const stopCamera = () => {
    const stream = videoRef.current?.srcObject;
    if (stream) {
      stream.getTracks().forEach((track) => track.stop());
      videoRef.current.srcObject = null;
    }
    setIsCameraOn(false);
  };

  const startCamera = async () => {
    try {
      const constraints = { video: true };
      if (selectedDeviceId) {
        constraints.video = { deviceId: { exact: selectedDeviceId } };
      }
      const stream = await navigator.mediaDevices.getUserMedia(constraints);
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        setIsCameraOn(true);
        refreshDevices();
      } else {
        stream.getTracks().forEach((track) => track.stop());
        setIsCameraOn(false);
      }
    } catch (err) {
      console.error("Camera error:", err);
      toast.error("Failed to access camera");
      setIsCameraOn(false);
    }
  };

  useEffect(() => {
    if (isCameraOn && selectedDeviceId) {
      stopCamera();
      startCamera();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedDeviceId]);

  const closeFaceModal = () => {
    stopCamera();
    setUpdatingFace(null);
    samples.forEach((s) => URL.revokeObjectURL(s.url));
    setSamples([]);
  };

  useEffect(() => {
    if (updatingFace) {
      setIsCameraOn(false);
      if (videoRef.current) videoRef.current.srcObject = null;
    } else {
      samples.forEach((s) => URL.revokeObjectURL(s.url));
      setSamples([]);
    }
  }, [updatingFace]);

  useEffect(() => {
    return () => {
      const stream = videoRef.current?.srcObject;
      if (stream) stream.getTracks().forEach((track) => track.stop());
      samples.forEach((s) => URL.revokeObjectURL(s.url));
    };
  }, []);

  const captureSample = () => {
    if (samples.length >= MAX_SAMPLES) return;
    if (!videoRef.current?.srcObject) {
      toast.warn("Please start camera first");
      return;
    }
    const video = videoRef.current;
    const canvas = canvasRef.current;
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

    canvas.toBlob(
      (blob) => {
        if (!blob) {
          toast.error("Failed to capture frame");
          return;
        }
        const url = URL.createObjectURL(blob);
        setSamples((prev) => [...prev, { blob, url }]);
      },
      "image/jpeg",
      0.9,
    );
  };

  const resetSamples = () => {
    samples.forEach((s) => URL.revokeObjectURL(s.url));
    setSamples([]);
  };

  const submitUpdateFaceMulti = async () => {
    if (samples.length === 0) {
      toast.warn("Please capture at least one sample first");
      return;
    }
    setIsProcessingFace(true);
    const formData = new FormData();
    samples.forEach((img, i) => {
      formData.append("images", img.blob, `update_face_${i}.jpg`);
    });

    try {
      const response = await fetch(
        `${API_URL}/users/${updatingFace.id}/update-face/multi`,
        {
          method: "POST",
          body: formData,
          headers: { "ngrok-skip-browser-warning": "true" },
        },
      );
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Update failed");
      toast.success(
        `Face updated successfully — ${data.embeddings_stored} angle${data.embeddings_stored === 1 ? "" : "s"} stored`,
      );
      closeFaceModal();
    } catch (err) {
      toast.error("Face update failed: " + err.message);
    } finally {
      setIsProcessingFace(false);
    }
  };

  return (
    <div className="max-w-6xl mx-auto w-full pt-2">
      {/* ── Header ── */}
      <div className="flex justify-between items-center mb-6">
        <h2 className="text-2xl font-extrabold text-text-main flex items-center gap-3">
          <div className="w-11 h-11 rounded-xl bg-primary/10 flex items-center justify-center">
            <svg
              xmlns="http://www.w3.org/2000/svg"
              className="h-6 w-6 text-primary"
              viewBox="0 0 20 20"
              fill="currentColor"
            >
              <path d="M13 6a3 3 0 11-6 0 3 3 0 016 0zM18 8a2 2 0 11-4 0 2 2 0 014 0zM14 15a4 4 0 00-8 0v3h8v-3zM6 8a2 2 0 11-4 0 2 2 0 014 0zM16 18v-3a5.972 5.972 0 00-.75-2.906A3.005 3.005 0 0119 15v3h-3zM4.75 12.094A5.973 5.973 0 004 15v3H1v-3a3 3 0 013.75-2.906z" />
            </svg>
          </div>
          Manage Users
        </h2>
        <div className="flex items-center gap-3">
          <span className="text-sm text-text-muted font-medium">
            {users.length} {users.length === 1 ? "user" : "users"}
            {typeFilter ? ` · ${TYPE_LABEL(typeFilter)}` : ""}
          </span>
          <button
            onClick={fetchUsers}
            className="p-3 rounded-xl bg-primary/8 text-primary hover:bg-primary/15 transition-all duration-200 cursor-pointer"
            title="Refresh List"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              className={`h-5 w-5 ${loading ? "animate-spin" : ""}`}
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
              />
            </svg>
          </button>
        </div>
      </div>

      {/* ── Filter tabs + search ── */}
      <div className="flex flex-col sm:flex-row sm:items-center gap-3 mb-5">
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => setTypeFilter(null)}
            className={`px-3 py-1.5 text-xs font-bold rounded-lg transition-all cursor-pointer ${
              typeFilter === null
                ? "bg-primary text-white shadow-sm"
                : "bg-primary/8 text-text-muted hover:bg-primary/15"
            }`}
          >
            All
          </button>
          {USER_TYPES.map((t) => (
            <button
              key={t.value}
              onClick={() => setTypeFilter(t.value)}
              className={`px-3 py-1.5 text-xs font-bold rounded-lg transition-all cursor-pointer ${
                typeFilter === t.value
                  ? "bg-primary text-white shadow-sm"
                  : "bg-primary/8 text-text-muted hover:bg-primary/15"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="sm:ml-auto">
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search name / MRN / contact…"
            className="w-full sm:w-72 bg-card border border-primary/15 focus:border-primary focus:ring-2 focus:ring-primary/15 rounded-xl px-4 py-2 text-sm font-medium placeholder-text-light/60 outline-none transition-all"
          />
        </div>
      </div>

      {error && (
        <div className="bg-danger/8 text-danger border border-danger/20 px-4 py-3 rounded-xl text-sm mb-5 animate-slide-up font-medium flex items-center gap-2">
          <svg
            xmlns="http://www.w3.org/2000/svg"
            className="h-4 w-4 shrink-0"
            viewBox="0 0 20 20"
            fill="currentColor"
          >
            <path
              fillRule="evenodd"
              d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7 4a1 1 0 11-2 0 1 1 0 012 0zm-1-9a1 1 0 00-1 1v4a1 1 0 102 0V6a1 1 0 00-1-1z"
              clipRule="evenodd"
            />
          </svg>
          {error}
        </div>
      )}

      {/* ── Patient List Card ── */}
      <div className="bg-card rounded-2xl shadow-md border border-primary/8 overflow-hidden">
        {loading && users.length === 0 ? (
          <div className="divide-y divide-primary/8">
            {[...Array(4)].map((_, i) => (
              <SkeletonRow key={i} />
            ))}
          </div>
        ) : users.length === 0 ? (
          <div className="p-14 text-center flex flex-col items-center animate-fade-in">
            <div className="w-16 h-16 bg-primary/8 rounded-2xl flex items-center justify-center text-primary mb-4">
              <svg
                xmlns="http://www.w3.org/2000/svg"
                className="h-8 w-8"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={1.5}
                  d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z"
                />
              </svg>
            </div>
            <p className="text-text-main font-semibold text-base">
              {typeFilter || search
                ? "No users match this filter"
                : "No users registered yet"}
            </p>
            <p className="text-sm text-text-light mt-1">
              {typeFilter || search
                ? "Try clearing the filter or search."
                : "Head over to the Register tab to add faces."}
            </p>
          </div>
        ) : (
          <ul className="divide-y divide-primary/6">
            {users.map((user, idx) => {
              const colors = getAvatarColors(user.name);
              return (
                <li
                  key={user.id}
                  className="p-5 sm:px-6 flex flex-col sm:flex-row items-center gap-5 hover:bg-primary/3 transition-all duration-200 animate-slide-up"
                  style={{ animationDelay: `${idx * 40}ms` }}
                >
                  <div
                    className="grow flex items-center w-full gap-4 cursor-pointer group"
                    onClick={() => navigate(`/patients/${user.id}`)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        navigate(`/patients/${user.id}`);
                      }
                    }}
                  >
                    <div
                      className="h-14 w-14 shrink-0 rounded-xl flex items-center justify-center font-bold text-lg shadow-sm transition-transform duration-200 group-hover:scale-105"
                      style={{
                        backgroundColor: colors.bg,
                        color: colors.text,
                        border: `1.5px solid ${colors.border}`,
                      }}
                    >
                      {user.name.charAt(0).toUpperCase()}
                    </div>
                    <div className="flex flex-col gap-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-semibold text-text-main text-lg group-hover:text-primary transition-colors duration-200">
                          {user.name}
                        </span>
                        {(() => {
                          const b = TYPE_BADGE[user.user_type] || TYPE_BADGE.PATIENT;
                          return (
                            <span className={`text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded ${b.bg} ${b.text}`}>
                              {b.label}
                            </span>
                          );
                        })()}
                      </div>
                      <span className="text-xs text-text-light font-mono">
                        {user.user_type === "PATIENT" && user.mrn ? `MRN: ${user.mrn}` : null}
                        {user.user_type === "DOCTOR" && user.specialty ? user.specialty : null}
                        {user.user_type === "EMPLOYEE" && user.role ? user.role : null}
                        {user.user_type === "VISITOR" && user.purpose ? user.purpose : null}
                        {(!user.user_type || (user.user_type === "PATIENT" && !user.mrn)) && `ID: ${user.id}`}
                      </span>
                    </div>
                  </div>

                  <div className="flex items-center gap-2.5 w-full sm:w-auto justify-end">
                    <button
                      onClick={() => openEditModal(user)}
                      className="px-4 py-2 text-sm whitespace-nowrap bg-primary/8 text-primary hover:bg-primary hover:text-white rounded-xl font-semibold transition-all duration-200 cursor-pointer flex items-center gap-2"
                    >
                      <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                      </svg>
                      Edit
                    </button>
                    <button
                      onClick={() => setUpdatingFace(user)}
                      className="px-4 py-2 text-sm whitespace-nowrap bg-accent-light/50 text-pink-700 dark:text-pink-300 hover:bg-accent hover:text-white rounded-xl font-semibold transition-all duration-200 cursor-pointer flex items-center gap-2"
                    >
                      <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 9a2 2 0 012-2h.93a2 2 0 001.664-.89l.812-1.22A2 2 0 0110.07 4h3.86a2 2 0 011.664.89l.812 1.22A2 2 0 0018.07 7H19a2 2 0 01-2 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V9z" />
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 13a3 3 0 11-6 0 3 3 0 016 0z" />
                      </svg>
                      Update Face
                    </button>
                    <button
                      onClick={() => handleDelete(user)}
                      className="px-4 py-2 text-sm whitespace-nowrap bg-danger/8 text-danger hover:bg-danger hover:text-white rounded-xl font-semibold transition-all duration-200 cursor-pointer flex items-center gap-2"
                    >
                      <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                      </svg>
                      Delete
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {/* ═══ MODAL: Edit User (type-aware) ═══ */}
      {editingUser && (
        <div className="fixed inset-0 bg-black/30 backdrop-blur-md z-50 flex items-center justify-center p-4">
          <div className="bg-card rounded-2xl shadow-2xl w-full max-w-lg overflow-hidden animate-scale-in border border-primary/10 flex flex-col max-h-[90vh]">
            {/* Header */}
            <div className="px-6 py-4 border-b border-primary/8 flex items-center justify-between shrink-0">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg bg-primary/10 flex items-center justify-center">
                  <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4 text-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
                  </svg>
                </div>
                <div>
                  <h3 className="text-base font-bold text-text-main">Edit {TYPE_LABEL(editingUser.user_type)}</h3>
                  <p className="text-xs text-text-light font-medium">
                    ID: {editingUser.id}
                    {editingUser.mrn ? ` · MRN: ${editingUser.mrn}` : ""}
                  </p>
                </div>
              </div>
              <button onClick={() => setEditingUser(null)} className="p-1.5 rounded-lg text-text-light hover:text-danger hover:bg-danger/8 cursor-pointer transition-all duration-200">
                <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            {/* Scrollable body */}
            <div className="overflow-y-auto p-6 flex flex-col gap-4">
              {/* Name + User Type (universal) */}
              <div className="flex flex-col gap-1.5">
                <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">Name <span className="text-danger">*</span></label>
                <input
                  type="text"
                  value={editForm.name}
                  onChange={(e) => updateEditField("name", e.target.value)}
                  autoFocus
                  placeholder="Full name"
                  className="w-full bg-background border border-primary/15 focus:border-primary focus:ring-2 focus:ring-primary/15 rounded-xl px-4 py-2.5 text-text-main font-semibold placeholder-text-light/50 outline-none transition-all duration-200"
                />
              </div>

              <div className="flex flex-col gap-1.5">
                <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">User Type</label>
                <CustomSelect
                  value={editForm.user_type}
                  onChange={(e) => updateEditField("user_type", e.target.value)}
                  options={USER_TYPES}
                />
              </div>

              {/* Contact (universal) */}
              <div className="flex flex-col gap-1.5">
                <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">Contact Number</label>
                <input
                  type="tel"
                  value={editForm.contact_number}
                  onChange={(e) => updateEditField("contact_number", e.target.value)}
                  placeholder="e.g. 9876543210"
                  maxLength={20}
                  className="w-full bg-background border border-primary/15 focus:border-primary focus:ring-2 focus:ring-primary/15 rounded-xl px-3 py-2.5 text-text-main text-sm font-semibold placeholder-text-light/50 outline-none transition-all duration-200"
                />
              </div>

              {/* ── PATIENT-specific ── */}
              {editForm.user_type === "PATIENT" && (
                <>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="flex flex-col gap-1.5">
                      <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">Age</label>
                      <input
                        type="number" min="0" max="150"
                        value={editForm.age}
                        onChange={(e) => updateEditField("age", e.target.value)}
                        placeholder="—"
                        className="w-full bg-background border border-primary/15 focus:border-primary focus:ring-2 focus:ring-primary/15 rounded-xl px-3 py-2.5 text-text-main text-sm font-semibold placeholder-text-light/50 outline-none transition-all duration-200"
                      />
                    </div>
                    <div className="flex flex-col gap-1.5">
                      <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">Gender</label>
                      <CustomSelect
                        value={editForm.gender}
                        onChange={(e) => updateEditField("gender", e.target.value)}
                        placeholder="—"
                        options={[
                          { value: "", label: "—" },
                          { value: "Male", label: "Male" },
                          { value: "Female", label: "Female" },
                          { value: "Other", label: "Other" },
                        ]}
                      />
                    </div>
                  </div>

                  <div className="flex flex-col gap-1.5">
                    <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">Date of Birth</label>
                    <input
                      type="date"
                      value={editForm.dob}
                      onChange={(e) => updateEditField("dob", e.target.value)}
                      className="w-full bg-background border border-primary/15 focus:border-primary focus:ring-2 focus:ring-primary/15 rounded-xl px-3 py-2.5 text-text-main text-sm font-semibold outline-none transition-all duration-200"
                    />
                  </div>

                  <div className="flex flex-col gap-1.5">
                    <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">Address</label>
                    <textarea
                      value={editForm.address}
                      onChange={(e) => updateEditField("address", e.target.value)}
                      placeholder="Street, city, …"
                      rows={2}
                      maxLength={500}
                      className="w-full bg-background border border-primary/15 focus:border-primary focus:ring-2 focus:ring-primary/15 rounded-xl px-3 py-2.5 text-text-main text-sm font-semibold placeholder-text-light/50 outline-none transition-all duration-200 resize-none"
                    />
                  </div>

                  <div className="grid grid-cols-2 gap-3">
                    <div className="flex flex-col gap-1.5">
                      <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">Department</label>
                      <input
                        type="text"
                        value={editForm.department}
                        onChange={(e) => updateEditField("department", e.target.value)}
                        placeholder="e.g. Cardiology"
                        maxLength={100}
                        className="w-full bg-background border border-primary/15 focus:border-primary focus:ring-2 focus:ring-primary/15 rounded-xl px-3 py-2.5 text-text-main text-sm font-semibold placeholder-text-light/50 outline-none transition-all duration-200"
                      />
                    </div>
                    <div className="flex flex-col gap-1.5">
                      <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">Doctor</label>
                      <input
                        type="text"
                        value={editForm.doctor}
                        onChange={(e) => updateEditField("doctor", e.target.value)}
                        placeholder="e.g. Dr. Sharma"
                        maxLength={100}
                        className="w-full bg-background border border-primary/15 focus:border-primary focus:ring-2 focus:ring-primary/15 rounded-xl px-3 py-2.5 text-text-main text-sm font-semibold placeholder-text-light/50 outline-none transition-all duration-200"
                      />
                    </div>
                  </div>

                  <div className="flex flex-col gap-1.5">
                    <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">Category</label>
                    <input
                      type="text"
                      value={editForm.category}
                      onChange={(e) => updateEditField("category", e.target.value)}
                      placeholder="e.g. IPD / OPD / VIP"
                      maxLength={100}
                      className="w-full bg-background border border-primary/15 focus:border-primary focus:ring-2 focus:ring-primary/15 rounded-xl px-3 py-2.5 text-text-main text-sm font-semibold placeholder-text-light/50 outline-none transition-all duration-200"
                    />
                  </div>
                </>
              )}

              {/* ── DOCTOR-specific ── */}
              {editForm.user_type === "DOCTOR" && (
                <>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="flex flex-col gap-1.5">
                      <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">OPD Department ID</label>
                      <input
                        type="number"
                        value={editForm.opd_department_id}
                        onChange={(e) => updateEditField("opd_department_id", e.target.value)}
                        placeholder="e.g. 1"
                        className="w-full bg-background border border-primary/15 focus:border-primary focus:ring-2 focus:ring-primary/15 rounded-xl px-3 py-2.5 text-text-main text-sm font-semibold placeholder-text-light/50 outline-none transition-all duration-200"
                      />
                    </div>
                    <div className="flex flex-col gap-1.5">
                      <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">OPD Room ID</label>
                      <input
                        type="number"
                        value={editForm.opd_room_id}
                        onChange={(e) => updateEditField("opd_room_id", e.target.value)}
                        placeholder="e.g. 2"
                        className="w-full bg-background border border-primary/15 focus:border-primary focus:ring-2 focus:ring-primary/15 rounded-xl px-3 py-2.5 text-text-main text-sm font-semibold placeholder-text-light/50 outline-none transition-all duration-200"
                      />
                    </div>
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">Specialty</label>
                    <input
                      type="text"
                      value={editForm.specialty}
                      onChange={(e) => updateEditField("specialty", e.target.value)}
                      placeholder="e.g. Cardiology"
                      maxLength={100}
                      className="w-full bg-background border border-primary/15 focus:border-primary focus:ring-2 focus:ring-primary/15 rounded-xl px-3 py-2.5 text-text-main text-sm font-semibold placeholder-text-light/50 outline-none transition-all duration-200"
                    />
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">Department (free-text)</label>
                    <input
                      type="text"
                      value={editForm.department}
                      onChange={(e) => updateEditField("department", e.target.value)}
                      placeholder="e.g. Outpatient"
                      maxLength={100}
                      className="w-full bg-background border border-primary/15 focus:border-primary focus:ring-2 focus:ring-primary/15 rounded-xl px-3 py-2.5 text-text-main text-sm font-semibold placeholder-text-light/50 outline-none transition-all duration-200"
                    />
                  </div>
                </>
              )}

              {/* ── EMPLOYEE-specific ── */}
              {editForm.user_type === "EMPLOYEE" && (
                <>
                  <div className="flex flex-col gap-1.5">
                    <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">Role</label>
                    <CustomSelect
                      value={editForm.role}
                      onChange={(e) => updateEditField("role", e.target.value)}
                      placeholder="Select role"
                      options={[
                        { value: "", label: "—" },
                        { value: "Nurse", label: "Nurse" },
                        { value: "Technician", label: "Technician" },
                        { value: "Admin", label: "Admin" },
                        { value: "Receptionist", label: "Receptionist" },
                        { value: "Security", label: "Security" },
                        { value: "Housekeeping", label: "Housekeeping" },
                      ]}
                    />
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">Staff Department</label>
                    <input
                      type="text"
                      value={editForm.staff_department}
                      onChange={(e) => updateEditField("staff_department", e.target.value)}
                      placeholder="e.g. Pathology"
                      maxLength={100}
                      className="w-full bg-background border border-primary/15 focus:border-primary focus:ring-2 focus:ring-primary/15 rounded-xl px-3 py-2.5 text-text-main text-sm font-semibold placeholder-text-light/50 outline-none transition-all duration-200"
                    />
                  </div>
                </>
              )}

              {/* ── VISITOR-specific ── */}
              {editForm.user_type === "VISITOR" && (
                <div className="flex flex-col gap-1.5">
                  <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">Purpose</label>
                  <input
                    type="text"
                    value={editForm.purpose}
                    onChange={(e) => updateEditField("purpose", e.target.value)}
                    placeholder="e.g. Patient visit, Vendor demo"
                    maxLength={200}
                    className="w-full bg-background border border-primary/15 focus:border-primary focus:ring-2 focus:ring-primary/15 rounded-xl px-3 py-2.5 text-text-main text-sm font-semibold placeholder-text-light/50 outline-none transition-all duration-200"
                  />
                </div>
              )}

              {/* Note (universal) */}
              <div className="flex flex-col gap-1.5">
                <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">Note</label>
                <textarea
                  value={editForm.note}
                  onChange={(e) => updateEditField("note", e.target.value)}
                  placeholder="Optional note"
                  rows={2}
                  maxLength={500}
                  className="w-full bg-background border border-primary/15 focus:border-primary focus:ring-2 focus:ring-primary/15 rounded-xl px-3 py-2.5 text-text-main text-sm font-semibold placeholder-text-light/50 outline-none transition-all duration-200 resize-none"
                />
              </div>

              {/* ── Relations panel ── */}
              <div className="flex flex-col gap-2 pt-3 border-t border-primary/10">
                <div className="flex items-center justify-between">
                  <label className="text-[10px] font-bold text-text-muted uppercase tracking-wider">
                    Relations {relations.length > 0 && <span className="text-primary">({relations.length})</span>}
                  </label>
                  {relationLoading && <span className="text-[10px] text-text-light">Loading…</span>}
                </div>

                {relations.length > 0 && (
                  <ul className="flex flex-col gap-1.5">
                    {relations.map((r) => {
                      const partnerId = r.user_id === editingUser.id ? r.related_user_id : r.user_id;
                      const badge = TYPE_BADGE[r.related_user_type] || TYPE_BADGE.PATIENT;
                      return (
                        <li
                          key={r.id}
                          className="flex items-center justify-between bg-background rounded-lg px-3 py-2 border border-primary/10"
                        >
                          <div className="flex items-center gap-2 min-w-0">
                            <span className={`text-[9px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded ${badge.bg} ${badge.text} shrink-0`}>
                              {badge.label}
                            </span>
                            <span className="text-sm font-semibold text-text-main truncate">
                              {r.related_user_name}
                            </span>
                            <span className="text-[10px] text-text-light font-mono shrink-0">
                              #{partnerId} · {r.relation_type}
                            </span>
                          </div>
                          <button
                            onClick={() => removeRelation(r.id)}
                            className="text-[10px] font-bold text-danger hover:bg-danger/10 px-2 py-1 rounded cursor-pointer"
                          >
                            Unlink
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                )}

                <div className="grid grid-cols-3 gap-2 mt-1">
                  <input
                    type="text"
                    value={relationQuery}
                    onChange={(e) => setRelationQuery(e.target.value)}
                    placeholder="Search user to link…"
                    className="col-span-2 w-full bg-background border border-primary/15 focus:border-primary focus:ring-2 focus:ring-primary/15 rounded-xl px-3 py-2 text-text-main text-sm font-semibold placeholder-text-light/50 outline-none transition-all duration-200"
                  />
                  <CustomSelect
                    value={relationPickType}
                    onChange={(e) => setRelationPickType(e.target.value)}
                    options={RELATION_TYPES}
                  />
                </div>

                {relationQuery.trim().length >= 2 && (
                  <div className="bg-background rounded-lg border border-primary/10 max-h-40 overflow-y-auto">
                    {relationSearching ? (
                      <div className="px-3 py-2 text-xs text-text-light">Searching…</div>
                    ) : relationResults.length === 0 ? (
                      <div className="px-3 py-2 text-xs text-text-light">No matches.</div>
                    ) : (
                      relationResults.map((u) => {
                        const b = TYPE_BADGE[u.user_type] || TYPE_BADGE.PATIENT;
                        return (
                          <button
                            key={u.id}
                            onClick={() => addRelation(u)}
                            disabled={relationAdding}
                            className="w-full flex items-center justify-between px-3 py-2 hover:bg-primary/8 transition-colors cursor-pointer disabled:opacity-50"
                          >
                            <div className="flex items-center gap-2 min-w-0">
                              <span className={`text-[9px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded ${b.bg} ${b.text} shrink-0`}>
                                {b.label}
                              </span>
                              <span className="text-sm font-semibold text-text-main truncate">{u.name}</span>
                            </div>
                            <span className="text-[10px] text-text-light font-mono">#{u.id}</span>
                          </button>
                        );
                      })
                    )}
                  </div>
                )}
              </div>
            </div>

            {/* Footer */}
            <div className="px-6 py-4 border-t border-primary/8 flex justify-end gap-2 shrink-0">
              <button
                onClick={() => setEditingUser(null)}
                className="px-4 py-2 rounded-xl text-text-muted hover:bg-primary/5 transition-all duration-200 font-semibold cursor-pointer"
              >
                Cancel
              </button>
              <button
                onClick={submitEditUser}
                disabled={!editForm.name.trim()}
                className="px-6 py-2 rounded-xl gradient-primary text-white hover:opacity-90 transition-all duration-200 disabled:opacity-40 disabled:cursor-not-allowed font-semibold cursor-pointer shadow-sm shadow-primary/20"
              >
                Save Changes
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ═══ MODAL: Update Face Multi ═══ */}
      {updatingFace && (
        <div className="fixed inset-0 bg-black/30 backdrop-blur-md z-50 flex items-center justify-center p-4">
          <div className="bg-card rounded-2xl shadow-2xl w-full max-w-lg overflow-hidden animate-scale-in border border-primary/10 flex flex-col">
            {/* Modal Header */}
            <div className="px-5 py-3.5 border-b border-primary/8 flex justify-between items-center">
              <h3 className="text-base font-bold flex items-center gap-2.5">
                <div className="w-8 h-8 rounded-lg bg-accent-light/50 flex items-center justify-center">
                  <svg
                    xmlns="http://www.w3.org/2000/svg"
                    className="h-4 w-4 text-pink-600 dark:text-pink-400"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M3 9a2 2 0 012-2h.93a2 2 0 001.664-.89l.812-1.22A2 2 0 0110.07 4h3.86a2 2 0 011.664.89l.812 1.22A2 2 0 0018.07 7H19a2 2 0 01-2 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V9z"
                    />
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M15 13a3 3 0 11-6 0 3 3 0 016 0z"
                    />
                  </svg>
                </div>
                Update Face:{" "}
                <span className="text-primary">{updatingFace.name}</span>
              </h3>
              <div className="flex items-center gap-2.5">
                {devices.length > 0 && (
                  <CameraSelect
                    devices={devices}
                    selectedDeviceId={selectedDeviceId}
                    onSelect={setSelectedDeviceId}
                    compact
                  />
                )}

                {isCameraOn ? (
                  <button
                    onClick={stopCamera}
                    className="text-xs px-3 py-1.5 bg-danger/10 text-danger hover:bg-danger/20 rounded-lg transition-all duration-200 font-semibold border border-danger/20 cursor-pointer"
                  >
                    Stop
                  </button>
                ) : (
                  <button
                    onClick={startCamera}
                    className="text-xs px-3 py-1.5 bg-success/10 text-green-700 dark:text-green-300 hover:bg-success/20 rounded-lg transition-all duration-200 font-semibold border border-success/20 cursor-pointer"
                  >
                    Start
                  </button>
                )}
                <button
                  onClick={closeFaceModal}
                  className="p-1.5 rounded-lg text-text-light hover:text-danger hover:bg-danger/8 cursor-pointer transition-all duration-200"
                >
                  <svg
                    xmlns="http://www.w3.org/2000/svg"
                    className="h-5 w-5"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M6 18L18 6M6 6l12 12"
                    />
                  </svg>
                </button>
              </div>
            </div>

            {/* Modal Body */}
            <div className="p-4 sm:p-5 flex flex-col items-center">
              <div className="w-full relative h-48 sm:h-64 bg-gray-900 rounded-xl overflow-hidden shadow-inner flex items-center justify-center mb-4 border border-white/5">
                <video
                  ref={videoRef}
                  autoPlay
                  playsInline
                  muted
                  className={`w-full h-full object-cover ${isCameraOn ? "" : "invisible"}`}
                />
                {!isCameraOn && (
                  <div className="absolute inset-0 flex flex-col items-center justify-center text-white/50 pointer-events-none gap-2">
                    <div className="w-12 h-12 rounded-xl bg-white/5 flex items-center justify-center border border-white/10">
                      <svg
                        xmlns="http://www.w3.org/2000/svg"
                        className="h-6 w-6 text-primary/50"
                        fill="none"
                        viewBox="0 0 24 24"
                        stroke="currentColor"
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          strokeWidth={1.5}
                          d="M3 9a2 2 0 012-2h.93a2 2 0 001.664-.89l.812-1.22A2 2 0 0110.07 4h3.86a2 2 0 011.664.89l.812 1.22A2 2 0 0018.07 7H19a2 2 0 01-2 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V9z"
                        />
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          strokeWidth={1.5}
                          d="M15 13a3 3 0 11-6 0 3 3 0 016 0z"
                        />
                      </svg>
                    </div>
                    <span className="text-xs font-medium">
                      Start camera to begin
                    </span>
                  </div>
                )}

                <div className="absolute top-2 right-2 bg-black/60 backdrop-blur-sm text-white text-xs font-bold px-2.5 py-1 rounded-full">
                  {samples.length} / {MAX_SAMPLES}
                </div>
                <canvas ref={canvasRef} className="hidden" />
              </div>

              {/* Sample Thumbnails */}
              <div className="w-full flex gap-2 flex-wrap min-h-16 p-2.5 bg-background/60 rounded-xl border border-primary/8 mb-4 justify-center">
                {samples.length === 0 ? (
                  <span className="text-xs text-text-light self-center font-medium">
                    No samples captured yet.
                  </span>
                ) : (
                  samples.map((s, i) => (
                    <div
                      key={i}
                      className="relative w-13 h-13 rounded-lg overflow-hidden border-2 border-primary/20 shadow-sm group hover:border-primary/40 transition-all duration-200"
                    >
                      <img
                        src={s.url}
                        alt={`sample-${i}`}
                        className="w-full h-full object-cover group-hover:scale-110 transition-transform duration-300"
                      />
                      <span className="absolute bottom-0 right-0 bg-primary text-white text-[9px] px-1 py-0.5 rounded-tl-lg leading-none font-bold">
                        {i + 1}
                      </span>
                    </div>
                  ))
                )}
              </div>

              <div className="w-full flex gap-3 mb-4">
                <button
                  onClick={captureSample}
                  disabled={
                    !isCameraOn ||
                    samples.length >= MAX_SAMPLES ||
                    isProcessingFace
                  }
                  className={`flex-1 px-4 py-2.5 rounded-xl font-semibold transition-all duration-200 flex items-center justify-center gap-2 cursor-pointer
                    ${
                      !isCameraOn ||
                      samples.length >= MAX_SAMPLES ||
                      isProcessingFace
                        ? "bg-primary/8 text-text-light cursor-not-allowed"
                        : "bg-accent-light/60 text-pink-800 dark:text-pink-300 hover:bg-accent/40 border border-accent/30"
                    }`}
                >
                  <svg
                    xmlns="http://www.w3.org/2000/svg"
                    className="h-4 w-4"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M3 9a2 2 0 012-2h.93a2 2 0 001.664-.89l.812-1.22A2 2 0 0110.07 4h3.86a2 2 0 011.664.89l.812 1.22A2 2 0 0018.07 7H19a2 2 0 01-2 2v9a2 2 0 01-2 2H5a2 2 0 01-2-2V9z"
                    />
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M15 13a3 3 0 11-6 0 3 3 0 016 0z"
                    />
                  </svg>
                  Capture
                </button>
                <button
                  onClick={resetSamples}
                  disabled={samples.length === 0 || isProcessingFace}
                  className={`px-4 py-2.5 rounded-xl font-semibold transition-all duration-200 cursor-pointer
                    ${
                      samples.length === 0 || isProcessingFace
                        ? "bg-primary/5 text-text-light cursor-not-allowed"
                        : "bg-card text-text-main border border-primary/15 hover:bg-danger/5 hover:text-danger hover:border-danger/20"
                    }`}
                >
                  Reset
                </button>
              </div>

              <button
                onClick={submitUpdateFaceMulti}
                disabled={samples.length === 0 || isProcessingFace}
                className={`w-full px-8 py-3 rounded-xl font-bold shadow-sm transition-all duration-200 flex items-center justify-center gap-2 cursor-pointer
                  ${
                    samples.length === 0 || isProcessingFace
                      ? "bg-primary/8 text-text-light cursor-not-allowed shadow-none"
                      : "gradient-primary text-white hover:opacity-90 shadow-lg shadow-primary/20 hover:-translate-y-px active:translate-y-0"
                  }`}
              >
                {isProcessingFace ? (
                  <>
                    <svg
                      className="animate-spin h-4.5 w-4.5 text-current opacity-70"
                      xmlns="http://www.w3.org/2000/svg"
                      fill="none"
                      viewBox="0 0 24 24"
                    >
                      <circle
                        className="opacity-25"
                        cx="12"
                        cy="12"
                        r="10"
                        stroke="currentColor"
                        strokeWidth="4"
                      ></circle>
                      <path
                        className="opacity-75"
                        fill="currentColor"
                        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
                      ></path>
                    </svg>
                    Updating…
                  </>
                ) : (
                  <>
                    <svg
                      xmlns="http://www.w3.org/2000/svg"
                      className="h-4.5 w-4.5"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={2.5}
                        d="M5 13l4 4L19 7"
                      />
                    </svg>
                    Submit New Faces ({samples.length})
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
