import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { PlaybackToolbar } from './PlaybackToolbar'
import { SetupWizard } from './SetupWizard'
import { WatchPage } from './WatchPage'
import './styles.css'
import './setup.css'
import './crt.css'
import './media.css'

const isSetup = window.location.pathname === '/setup'
const isWatch = /^\/watch\/\d+/.test(window.location.pathname)
const Root = isSetup ? SetupWizard : isWatch ? WatchPage : App

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Root />
      {!isSetup && !isWatch && <PlaybackToolbar />}
    </BrowserRouter>
  </React.StrictMode>,
)
