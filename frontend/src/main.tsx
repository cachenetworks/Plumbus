import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { CatalogApp } from './CatalogApp'
import { PlaybackToolbar } from './PlaybackToolbar'
import { SetupWizard } from './SetupWizard'
import { WatchPage } from './WatchPage'
import './styles.css'
import './setup.css'
import './crt.css'
import './media.css'

const path = window.location.pathname
const isSetup = path === '/setup'
const isWatch = /^\/watch\/\d+/.test(path)
const isCatalog = path === '/browse' || path === '/search' || path === '/collections' || /^\/(media|movie)\/\d+$/.test(path)
const Root = isSetup ? SetupWizard : isWatch ? WatchPage : isCatalog ? CatalogApp : App

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Root />
      {!isSetup && !isWatch && <PlaybackToolbar />}
    </BrowserRouter>
  </React.StrictMode>,
)
