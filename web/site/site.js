document.querySelectorAll(".faq-section .faq-item").forEach((e,s)=>{const a=e.querySelector(".faq-q"),n=e.querySelector(".faq-a");if(!a||!n)return;const t=document.createElement("div");t.className="faq-panel";const l=document.createElement("div");l.className="faq-panel-inner",n.replaceWith(t),t.appendChild(l),l.appendChild(n);const o=s===0||e.classList.contains("open");e.classList.toggle("open",o),a.setAttribute("aria-expanded",o?"true":"false"),a.addEventListener("click",()=>{const i=!e.classList.contains("open");document.querySelectorAll(".faq-section .faq-item").forEach(c=>{c.classList.toggle("open",c===e&&i),c.querySelector(".faq-q")?.setAttribute("aria-expanded",c===e&&i?"true":"false")})})});function escapeHtml(e){return String(e??"").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;")}function afterPageLoad(e){const s=()=>{typeof requestIdleCallback=="function"?requestIdleCallback(()=>e(),{timeout:2e3}):setTimeout(e,0)};document.readyState==="complete"?s():window.addEventListener("load",s,{once:!0})}function pictureHtml(e,s,a,n){const t=`/static/site/assets/${e}`;return`<picture>
            <source type="image/avif" srcset="${t}.avif 1x, ${t}@2x.avif 2x" />
            <source type="image/webp" srcset="${t}.webp 1x, ${t}@2x.webp 2x" />
            <img src="${t}.webp" width="${s}" height="${a}" alt="${escapeHtml(n)}" loading="lazy" decoding="async" />
          </picture>`}function soldImageHtml(e,s){const a=String(e||"/static/site/assets/sold-1.webp"),n=a.match(/\/static\/site\/assets\/(sold-\d+)/);return n?pictureHtml(n[1],301,220,s):`<img src="${escapeHtml(a)}" width="301" height="220" alt="${escapeHtml(s)}" loading="lazy" decoding="async" />`}async function renderGoneFast(){const e=document.getElementById("sold-cards");if(e)try{const n=(await(await fetch("/api/public/gone-fast")).json().catch(()=>({}))).items||[];if(!n.length)return;e.innerHTML=n.map(t=>`<article class="sold-card">
          <div class="sold-img">
            ${soldImageHtml(t.image,t.locality||"")}
            <span class="sold-badge">${escapeHtml(t.badge||"PRONAJATO")}</span>
          </div>
          <div class="sold-meta">
            <div class="sold-top"><span>${escapeHtml(t.locality||"")}</span><span>${escapeHtml(t.price||"")}</span></div>
            <p class="spec">${escapeHtml(t.spec||"")}</p>
            <p class="when">${escapeHtml(t.when||"")}</p>
          </div>
        </article>`).join("")}catch{}}async function renderNewToday(){const e=document.getElementById("urg-new-today");if(e)try{const a=await(await fetch("/api/public/stats")).json().catch(()=>({}));if(!Number.isFinite(a.new_today))return;e.textContent=`${Number(a.new_today).toLocaleString("cs-CZ")} `}catch{}}afterPageLoad(()=>{renderGoneFast(),renderNewToday()});
