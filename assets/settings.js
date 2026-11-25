(function (root) {
    'use strict';
    const $ = id => document.getElementById(id);
    const t = text => root.AppUI.translate(text);
    let opening;
    async function api(path, body) {
        const response = await fetch(path, body === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json','X-StreamChrome':'1'}, body:JSON.stringify(body)});
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || t('Request failed.'));
        return data;
    }
    function message(id, text, error = false) {
        $(id).textContent = text;
        $(id).dataset.error = String(error);
    }
    function integrationState(state) {
        for (const [id,key] of [['settingsAutorun','autorun'],['settingsDriveIcon','drive_icon']]) {
            $(id).checked = Boolean(state[key]);
            $(id).disabled = !state.available;
        }
        message('integrationStatus', state.reason ? t(state.reason) : t('Changes apply to this drive and this Windows account.'));
    }
    async function open() {
        if (opening) return opening;
        opening = (async () => {
            try {
                const data = await api('/api/app-settings');
                integrationState(data.integration);
                $('updateVersion').textContent = `${t('Current version')}: ${data.version}`;
                const paths = $('settingsPaths');
                paths.replaceChildren();
                for (const [label,path] of [[t('Movies'),data.paths.movies],[t('Series'),data.paths.series],[t('Application data'),data.paths.data]]) {
                    const term=document.createElement('dt'), value=document.createElement('dd');
                    term.textContent=label; value.textContent=path; paths.append(term,value);
                }
                message('appSettingsStatus','');
            } catch (error) { message('appSettingsStatus',error.message,true); }
        })().finally(()=>{opening=null;});
        return opening;
    }
    function bind() {
        $('settingsAddons').addEventListener('click',()=>root.openAddonsModal());
        for (const button of document.querySelectorAll('[data-settings-folder]')) button.addEventListener('click', async()=>{
            try { await api('/api/system/open-folder',{folder:button.dataset.settingsFolder}); }
            catch(error) { message('appSettingsStatus',error.message,true); }
        });
        $('settingsScan').addEventListener('click', async()=>{
            const button=$('settingsScan'); button.disabled=true;
            try { await api('/api/catalog/scan',{}); message('appSettingsStatus',t('Library scan started.')); }
            catch(error) { message('appSettingsStatus',error.message,true); }
            finally { button.disabled=false; }
        });
        for (const [id,key] of [['settingsAutorun','autorun'],['settingsDriveIcon','drive_icon']]) $(id).addEventListener('change',async()=>{
            const input=$(id), chosen=input.checked; input.disabled=true;
            try { integrationState(await api('/api/app-settings',{[key]:chosen})); }
            catch(error) { input.checked=!chosen; message('integrationStatus',error.message,true); }
            finally { input.disabled=false; }
        });
        $('checkUpdates').addEventListener('click', async()=>{
            const button=$('checkUpdates'); button.disabled=true; $('applyUpdate').hidden=true;
            message('updateStatus',t('Checking GitHub…'));
            try {
                const data=await api('/api/updates/check',{});
                $('applyUpdate').hidden=!data.available || !data.supported;
                message('updateStatus',data.reason ? t(data.reason) : data.available ? `${t('New version available')}: ${data.latest_version}` : t('You are up to date.'));
            } catch(error) { message('updateStatus',error.message,true); }
            finally { button.disabled=false; }
        });
        $('applyUpdate').addEventListener('click',async()=>{
            const button=$('applyUpdate'); button.disabled=true;
            message('updateStatus',t('Downloading and verifying the update…'));
            try {
                const update = await api('/api/updates/apply',{});
                message('updateStatus',t('Restarting to finish the update…'));
                const retry=setInterval(async()=>{
                    try {
                        const status = await api('/api/status');
                        if (status.version === update.latest_version) { clearInterval(retry); root.location.reload(); }
                    } catch {}
                },2000);
                setTimeout(()=>{clearInterval(retry);message('updateStatus',t('If the server has closed, reopen StreamChrome Server to continue.'));},60000);
            } catch(error) { message('updateStatus',error.message,true); button.disabled=false; }
        });
    }
    root.StreamSettings = {open, confirmExit:()=>new Promise(resolve=>{
        const dialog=$('exitConfirmation'); dialog.returnValue='';
        dialog.addEventListener('close',()=>resolve(dialog.returnValue==='exit'),{once:true});
        dialog.showModal();
    })};
    if (document.readyState==='loading') document.addEventListener('DOMContentLoaded',bind);
    else bind();
})(window);
