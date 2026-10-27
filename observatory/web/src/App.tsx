import { Navigate, NavLink, Route, Routes } from "react-router-dom";

import { HealthBadge } from "./components/HealthBadge";
import { ReviewPage } from "./pages/review/ReviewPage";

export function App() {
  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">Observatory</span>
        <nav>
          <NavLink to="/review">Review</NavLink>
        </nav>
        <HealthBadge />
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/review" replace />} />
          <Route path="/review" element={<ReviewPage />} />
          <Route path="/review/:kind/:id" element={<ReviewPage />} />
          <Route path="*" element={<p className="empty">No page here.</p>} />
        </Routes>
      </main>
    </div>
  );
}
