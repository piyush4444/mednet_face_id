import { useState, useRef, useEffect } from 'react';

/**
 * Premium custom dropdown for camera device selection.
 * Replaces native <select> with a styled, animated, accessible dropdown.
 */
export default function CameraSelect({ devices, selectedDeviceId, onSelect, compact = false }) {
  const [open, setOpen] = useState(false);
  const wrapperRef = useRef(null);

  useEffect(() => {
    const handler = (e) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, []);

  const selected = devices.find((d) => d.deviceId === selectedDeviceId);
  const selectedLabel = selected?.label || 'Select Camera';

  const formatLabel = (label, idx) => {
    if (!label) return `Camera ${idx + 1}`;
    if (label.length > 36) return label.slice(0, 33) + '…';
    return label;
  };

  return (
    <div ref={wrapperRef} className="relative" style={{ zIndex: 50 }}>
      {/* ── Trigger ── */}
      <button
        type="button"
        onClick={() => setOpen((p) => !p)}
        className={`
          group flex items-center gap-2 cursor-pointer
          bg-card/80 backdrop-blur-sm
          border border-primary/15 hover:border-primary/40
          rounded-xl shadow-sm hover:shadow-md
          text-text-main font-semibold
          transition-all duration-200 outline-none
          focus-visible:ring-2 focus-visible:ring-primary/30
          ${compact ? 'text-xs px-2.5 py-1.5' : 'text-sm px-3.5 py-2'}
          ${open ? 'border-primary/50 shadow-md ring-2 ring-primary/15' : ''}
        `}
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          className={`${compact ? 'h-3.5 w-3.5' : 'h-4 w-4'} text-primary shrink-0`}
          fill="none" viewBox="0 0 24 24" stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
            d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
        </svg>

        <span className={`truncate ${compact ? 'max-w-25 sm:max-w-35' : 'max-w-32.5 sm:max-w-45'}`}>
          {formatLabel(selectedLabel, 0)}
        </span>

        <svg
          xmlns="http://www.w3.org/2000/svg"
          className={`${compact ? 'h-3 w-3' : 'h-3.5 w-3.5'} text-text-light transition-transform duration-200 shrink-0 ${open ? 'rotate-180' : ''}`}
          fill="none" viewBox="0 0 24 24" stroke="currentColor"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {/* ── Panel ── */}
      <div
        className={`
          absolute right-0 mt-2 w-72 max-w-[90vw]
          bg-card/95 backdrop-blur-2xl
          border border-primary/12
          rounded-2xl shadow-2xl shadow-primary/10
          overflow-hidden
          transition-all duration-200 origin-top-right
          ${open
            ? 'opacity-100 scale-100 translate-y-0 pointer-events-auto'
            : 'opacity-0 scale-95 -translate-y-1 pointer-events-none'
          }
        `}
      >
        <div className="px-4 py-2.5 border-b border-primary/8 bg-primary/3">
          <p className="text-[11px] font-bold text-text-muted uppercase tracking-widest flex items-center gap-1.5">
            <svg xmlns="http://www.w3.org/2000/svg" className="h-3 w-3 text-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
            </svg>
            Available Cameras
          </p>
        </div>

        <div className="py-1 max-h-56 overflow-y-auto">
          {devices.map((device, idx) => {
            const isSelected = device.deviceId === selectedDeviceId;
            return (
              <button
                key={device.deviceId}
                type="button"
                onClick={() => { onSelect(device.deviceId); setOpen(false); }}
                className={`
                  w-full flex items-center gap-3 px-4 py-3 text-left
                  transition-all duration-150 cursor-pointer
                  ${isSelected
                    ? 'bg-primary/8 text-primary'
                    : 'text-text-main hover:bg-primary/4'
                  }
                `}
              >
                <span
                  className={`
                    shrink-0 flex items-center justify-center
                    w-5 h-5 rounded-full border-2 transition-all duration-150
                    ${isSelected ? 'border-primary bg-primary' : 'border-text-light/40 bg-transparent'}
                  `}
                >
                  {isSelected && (
                    <svg className="w-3 h-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                    </svg>
                  )}
                </span>

                <div className="flex flex-col min-w-0 flex-1">
                  <span className={`text-sm font-semibold truncate ${isSelected ? 'text-primary' : ''}`}>
                    {formatLabel(device.label, idx)}
                  </span>
                  <span className="text-[10px] text-text-light font-mono truncate">
                    {device.deviceId ? device.deviceId.slice(0, 16) + '…' : 'default'}
                  </span>
                </div>

                {isSelected && (
                  <span className="shrink-0 text-[10px] font-bold bg-primary/15 text-primary px-2 py-0.5 rounded-full">
                    ACTIVE
                  </span>
                )}
              </button>
            );
          })}

          {devices.length === 0 && (
            <div className="px-4 py-8 text-center">
              <svg xmlns="http://www.w3.org/2000/svg" className="h-8 w-8 text-text-light/40 mx-auto mb-2" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                  d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
              </svg>
              <p className="text-xs text-text-muted">No cameras detected</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
