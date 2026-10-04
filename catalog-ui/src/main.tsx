import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { HashRouter } from 'react-router-dom'
import App from './App'
import './index.css'

// A file dropped past a drop zone (on the canvas, on a panel) is OPENED
// by the browser instead of being handed to the page - the tab leaves
// the interface. The drop zone of the versions screen handles its own
// events and stops propagation; everything else is swallowed here.
window.addEventListener('dragover', e => e.preventDefault())
window.addEventListener('drop', e => e.preventDefault())

// HashRouter: the interface is served by the catalog service as static
// files under /ui/, so routes in the hash need no server fallback to
// index.html. The addresses of the screens are the contract for links
// from COSMOS and the CDPM panel: <CREW_CATALOG_URL>/ui/#/...
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <HashRouter>
      <App />
    </HashRouter>
  </StrictMode>,
)
