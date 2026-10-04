import { Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/layout/Layout'
import RecordsPage from './pages/RecordsPage'
import RecordPage from './pages/RecordPage'
import DictionariesPage from './pages/DictionariesPage'
import DictionaryPage from './pages/DictionaryPage'
import RequestsPage from './pages/RequestsPage'
import RequestPage from './pages/RequestPage'
import ExecutorsPage from './pages/ExecutorsPage'
import VersionsPage from './pages/VersionsPage'

// Addresses of the five screens - the contract of links from COSMOS and
// the CDPM panel (project step 5, section 6.1). A static segment is
// registered before a dynamic one: /requests/new is a form, not a
// request named "new"; /versions/compare is the comparison, not a
// version named "compare".
export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Navigate to="/records" replace />} />
        <Route path="/records" element={<RecordsPage />} />
        <Route path="/records/:section/:id" element={<RecordPage />} />
        <Route path="/dictionaries" element={<DictionariesPage />} />
        <Route path="/dictionaries/:key" element={<DictionaryPage />} />
        <Route path="/requests" element={<RequestsPage />} />
        <Route path="/requests/new" element={<RequestPage create />} />
        <Route path="/requests/:id" element={<RequestPage />} />
        <Route path="/executors" element={<ExecutorsPage />} />
        <Route path="/versions" element={<VersionsPage />} />
        <Route path="/versions/compare" element={<VersionsPage compare />} />
        <Route path="/versions/:version" element={<VersionsPage />} />
        <Route path="*" element={<Navigate to="/records" replace />} />
      </Routes>
    </Layout>
  )
}
