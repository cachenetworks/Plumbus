import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Route, Routes, useLocation } from 'react-router-dom'
import App from './App'
import { CatalogApp } from './CatalogApp'
import { SetupWizard } from './SetupWizard'
import { WatchPage } from './WatchPage'
import './styles.css'
import './setup.css'
import './crt.css'
import './media.css'

const catalogRootPaths = new Set([
  '/browse',
  '/search',
  '/categories',
  '/collections',
  '/watched',
])

function isCatalogPath(pathname:string){
  return catalogRootPaths.has(pathname)||/^\/(media|movie)\/\d+$/.test(pathname)
}

function CatalogRoutes(){
  return <Routes>
    <Route path="/browse" element={<CatalogApp/>}/>
    <Route path="/search" element={<CatalogApp/>}/>
    <Route path="/categories" element={<CatalogApp/>}/>
    <Route path="/collections" element={<CatalogApp/>}/>
    <Route path="/watched" element={<CatalogApp/>}/>
    <Route path="/media/:id" element={<CatalogApp/>}/>
    <Route path="/movie/:id" element={<CatalogApp/>}/>
  </Routes>
}

function RoutedRoot(){
  const {pathname}=useLocation()
  const isSetup=pathname==='/setup'
  const isWatch=/^\/watch\/\d+/.test(pathname)
  const isCatalog=isCatalogPath(pathname)
  return isSetup?<SetupWizard/>:isWatch?<WatchPage/>:isCatalog?<CatalogRoutes/>:<App/>
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter><RoutedRoot/></BrowserRouter>
  </React.StrictMode>,
)
