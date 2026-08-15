import Link from "next/link";
import { ArrowRight, Shield, Briefcase } from "lucide-react";

export default function HomePage() {
  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100 dark:from-slate-950 dark:to-slate-900">
      {/* Header */}
      <header className="border-b bg-white/50 dark:bg-slate-900/50 backdrop-blur-sm">
        <div className="container mx-auto px-4 py-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Shield className="w-8 h-8 text-blue-600" />
            <span className="text-2xl font-bold">VETTED</span>
          </div>
          <nav className="flex gap-6">
            <Link href="/login" className="text-sm hover:text-blue-600 transition">
              Login
            </Link>
            <Link href="/uhrs" className="text-sm hover:text-blue-600 transition">
              UHRS Sim
            </Link>
            <Link href="/talent" className="text-sm hover:text-blue-600 transition">
              For Talent
            </Link>
            <Link href="/business" className="text-sm hover:text-blue-600 transition">
              For Business
            </Link>
          </nav>
        </div>
      </header>

      {/* Hero */}
      <main className="container mx-auto px-4 py-20">
        <div className="text-center max-w-4xl mx-auto space-y-8">
          <h1 className="text-6xl font-bold tracking-tight">
            The Trust Infrastructure
            <br />
            <span className="text-blue-600">for Cross-Border Tech Talent</span>
          </h1>
          
          <p className="text-xl text-slate-600 dark:text-slate-400 max-w-2xl mx-auto">
            Biometric verification, skill assessment labs, and automated milestone 
            payments. Eliminating fraud in offshore hiring.
          </p>

          {/* Dual CTA */}
          <div className="grid md:grid-cols-2 gap-6 mt-12 max-w-3xl mx-auto">
            {/* Talent Portal */}
            <Link href="/talent">
              <div className="group p-8 border-2 border-slate-200 dark:border-slate-800 rounded-2xl hover:border-blue-500 dark:hover:border-blue-500 transition-all hover:shadow-xl bg-white dark:bg-slate-900">
                <div className="w-12 h-12 bg-blue-100 dark:bg-blue-950 rounded-lg flex items-center justify-center mb-4">
                  <Shield className="w-6 h-6 text-blue-600" />
                </div>
                <h3 className="text-2xl font-bold mb-2">VettedME</h3>
                <p className="text-slate-600 dark:text-slate-400 mb-4">
                  Get verified, build your trust passport, and access high-value 
                  contracts with Western companies.
                </p>
                <div className="flex items-center text-blue-600 font-semibold group-hover:gap-2 transition-all">
                  Start Verification
                  <ArrowRight className="w-4 h-4 ml-1 group-hover:translate-x-1 transition" />
                </div>
              </div>
            </Link>

            {/* Business Portal */}
            <Link href="/business">
              <div className="group p-8 border-2 border-slate-200 dark:border-slate-800 rounded-2xl hover:border-green-500 dark:hover:border-green-500 transition-all hover:shadow-xl bg-white dark:bg-slate-900">
                <div className="w-12 h-12 bg-green-100 dark:bg-green-950 rounded-lg flex items-center justify-center mb-4">
                  <Briefcase className="w-6 h-6 text-green-600" />
                </div>
                <h3 className="text-2xl font-bold mb-2">VettedPay</h3>
                <p className="text-slate-600 dark:text-slate-400 mb-4">
                  Hire verified talent with zero fraud risk. Automated escrow and 
                  milestone payments with biometric release.
                </p>
                <div className="flex items-center text-green-600 font-semibold group-hover:gap-2 transition-all">
                  View Talent Pool
                  <ArrowRight className="w-4 h-4 ml-1 group-hover:translate-x-1 transition" />
                </div>
              </div>
            </Link>
          </div>

          {/* Stats */}
          <div className="grid grid-cols-3 gap-8 mt-20 max-w-3xl mx-auto">
            <div>
              <div className="text-4xl font-bold text-blue-600">100%</div>
              <div className="text-sm text-slate-600 dark:text-slate-400 mt-1">
                Biometric Verified
              </div>
            </div>
            <div>
              <div className="text-4xl font-bold text-green-600">50%+</div>
              <div className="text-sm text-slate-600 dark:text-slate-400 mt-1">
                Cost Savings
              </div>
            </div>
            <div>
              <div className="text-4xl font-bold text-purple-600">0</div>
              <div className="text-sm text-slate-600 dark:text-slate-400 mt-1">
                Identity Fraud
              </div>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
