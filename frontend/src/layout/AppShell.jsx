/**
 * AppShell — the admin dashboard frame: persistent Sidebar + Topbar + a
 * scrollable content area. Replaces the old top-tab MainLayout.
 *
 * Providers that the whole authed app needs live here: SocketProvider (live
 * WS) and FacilityProvider (the topbar facility switcher). Mounted only after
 * auth resolves, so the WebSocket never opens on the login screen.
 */
import Sidebar from "./Sidebar";
import Topbar from "./Topbar";
import { SocketProvider } from "../hooks/useSocket";
import { FacilityProvider } from "../hooks/useFacility";

export default function AppShell({ children }) {
  return (
    <SocketProvider>
      <FacilityProvider>
        <div className="min-h-screen bg-background text-text-main font-sans flex">
          <Sidebar />
          <div className="flex-1 min-w-0 flex flex-col">
            <Topbar />
            <main className="flex-1 min-w-0 px-4 sm:px-6 lg:px-8 py-6">
              {children}
            </main>
          </div>
        </div>
      </FacilityProvider>
    </SocketProvider>
  );
}
