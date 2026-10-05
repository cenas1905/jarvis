"use strict";
async function homeRequest(path,method="GET") {
  const response=await fetch(path,{method,headers:{Authorization:"Bearer "+getToken()},cache:"no-store"});
  const payload=await response.json();
  if(!response.ok) throw new Error(payload.detail||"Bağlantı kurulamadı");
  return payload;
}
const homeInfo=document.getElementById("home-info");
document.getElementById("home-pair").onclick=async()=>{
  if(!confirm("Eve geliş modu açılacak ve varsa eski eşleştirme anahtarı iptal edilecek. Telefon bildirim gönderene kadar bilgisayar mikrofonu kapanacak. Devam?"))return;
  try{
    const result=await homeRequest("/api/home/pair","POST");
    homeInfo.textContent="HTTP POST adresi:\n"+result.heartbeat_url+"\n\nAuthorization başlığı:\nBearer "+result.presence_key+"\n\nHer 60 saniye; yalnızca EV Wi-Fi ağına bağlıyken.\nTelefon bildirimi bekleniyor; bilgisayar mikrofonu kapalı.";
  }catch(e){homeInfo.textContent=e.message;}
};
document.getElementById("home-status").onclick=async()=>{
  try{const result=await homeRequest("/api/home/status");
    homeInfo.textContent=!result.enabled?"Eve geliş modu kapalı; normal mikrofon modu.":result.home?"Telefon evde: Hey Jarvis bekleniyor. Kalan doğrulama süresi: "+result.expires_in+" sn":"Telefon doğrulanmadı/evde değil: bilgisayar mikrofonu kapalı.";
  }catch(e){homeInfo.textContent=e.message;}
};
document.getElementById("home-disable").onclick=async()=>{
  if(!confirm("Eve geliş kontrolü kapanacak, anahtar iptal edilecek ve bilgisayar normal konuşma moduna dönecek. Devam?"))return;
  try{await homeRequest("/api/home/disable","POST"); homeInfo.textContent="Normal bilgisayar mikrofon moduna dönüldü.";}
  catch(e){homeInfo.textContent=e.message;}
};
