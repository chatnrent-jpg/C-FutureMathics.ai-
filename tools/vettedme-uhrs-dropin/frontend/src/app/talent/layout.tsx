import Link from "next/link";
import { Shield, User, FileText, Award } from "lucide-react";

export default function TalentLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-screen bg-slate-50 dark:bg-slate-950">
      {/* Header */}
      <header className="border-b bg-white dark:bg-slate-900">
        <div className="container mx-auto px-4">
          <div className="flex items-center justify-between h-16">
            <Link href="/" className="flex items-center gap-2">
              <Shield className="w-6 h-6 text-blue-600" />
              <span className="text-xl font-bold">VettedME</span>
            </Link>
            
            <nav className="flex gap-6 items-center">
              <Link 
                href="/talent/dashboard" 
                className="text-sm hover:text-blue-600 transition"
              >
                Dashboard
              </Link>
              <Link 
                href="/talent/assessment" 
                className="text-sm hover:text-blue-600 transition"
              >
                Assessment
              </Link>
              <Link
                href="/uhrs"
                className="text-sm hover:text-blue-600 transition"
              >
                UHRS Sim
              </Link>
              <Link 
                href="/talent/passport" 
                className="text-sm hover:text-blue-600 transition"
              >
                My Passport
              </Link>
              <div className="w-8 h-8 bg-blue-100 dark:bg-blue-950 rounded-full flex items-center justify-center">
                <User className="w-4 h-4 text-blue-600" />
              </div>
            </nav>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main>{children}</main>

      {/* Footer */}
      <footer className="border-t bg-white dark:bg-slate-900 mt-20">
        <div className="container mx-auto px-4 py-8">
          <div className="text-center text-sm text-slate-600 dark:text-slate-400">
            © 2026 VETTED. Trust infrastructure for cross-border talent.
          </div>
        </div>
      </footer>
    </div>
  );
}
