import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, useLocation } from 'react-router-dom'
import App from './App'
import { CatalogApp } from './CatalogApp'
import { PlaybackToolbar } from './PlaybackToolbar'
import { SetupWizard } from './SetupWizard'
import { WatchPage } from './WatchPage'
import './styles.css'
import './setup.css'
import './crt.css'
import './media.css'

function RoutedRoot(){
  const {pathname}=useLocation()
  const isSetup=pathname==='/setup'
  const isWatch=/^\/watch\/\d+/.test(pathname)
  const isCatalog=pathname==='/browse'||pathname==='/search'||pathname==='/collections'||/^\/(media|movie)\/\d+$/.test(pathname)
  return <>
    {isSetup?<SetupWizard/>:isWatch?<WatchPage/>:isCatalog?<CatalogApp/>:<App/>}
    {!isSetup&&!isWatch&&<PlaybackToolbar/>}
  </>
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter><RoutedRoot/></BrowserRouter>
  </React.StrictMode>,
)
