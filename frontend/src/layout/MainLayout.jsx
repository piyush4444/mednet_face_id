import Header from '../components/Header';
import { SocketProvider } from '../hooks/useSocket';
import synoraLogo from '../assets/Synora Logo.png';

export default function MainLayout({ children }) {
  return (
    <SocketProvider>
      <div className="min-h-screen bg-background text-text-main flex flex-col font-sans">
        <Header />

        {/* Main Content Area — wide by default; individual pages can
            self-constrain (e.g. PatientProfile uses max-w-3xl). */}
        <main className="grow w-full mx-auto px-4 sm:px-6 lg:px-8 py-6 transition-all duration-200">
          {children}
        </main>

        {/* Footer */}
        <footer className="mt-auto border-t border-primary/8">
          <div className="w-full mx-auto px-4 sm:px-6 lg:px-8">
            <div className="py-5 flex flex-col sm:flex-row items-center justify-between gap-2">
              <div className="flex items-center gap-2.5">
                <div className="w-5 h-5 rounded-md overflow-hidden bg-white shadow-sm ring-1 ring-black/5">
                  <img src={synoraLogo} alt="Iris by Synora AI Labs" className="w-full h-full object-cover" />
                </div>
                <span className="text-sm font-semibold text-text-muted">
                  Iris &middot; &copy; {new Date().getFullYear()} Synora AI Labs
                </span>
              </div>
              <span className="text-xs text-text-light">
                Built with precision for face recognition
              </span>
            </div>
          </div>
        </footer>
      </div>
    </SocketProvider>
  );
}
