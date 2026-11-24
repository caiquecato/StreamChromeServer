(function(root){
    'use strict';
    const KEY='streamchrome.appearance.v1';
    const LAYOUTS=['neutral','cinema','classic','studio','library','glass'];
    const THEMES=['dark','light'];
    const LANGUAGES=['en','pt','es'];
    function normalizePreferences(input){
        input=input&&typeof input==='object'?input:{};
        return {layout:LAYOUTS.includes(input.layout)?input.layout:'cinema',theme:THEMES.includes(input.theme)?input.theme:'dark',language:LANGUAGES.includes(input.language)?input.language:'en'};
    }
    function readPreferences(storage){
        try{return normalizePreferences(JSON.parse(storage.getItem(KEY)));}
        catch{return normalizePreferences({});}
    }
    function createController({document,storage,i18n,onChange,optionConstructor=root.Option}={}){
        let preferences=readPreferences(storage);
        function translateTo(text,language){
            const source=String(text);
            const translated=i18n.translate(source,language);
            if(translated!==source)return translated;
            const provider=source.match(/^(SubDL|OpenSubtitles): (.*)$/s);
            return provider?provider[1]+': '+i18n.translate(provider[2],language):translated;
        }
        const translate=text=>translateTo(text,preferences.language);
        function text(element,message){
            if(!element)return;
            const canonical=translateTo(message,'en');
            element.removeAttribute?.('data-i18n');
            element.removeAttribute?.('data-i18n-parts');
            element.setAttribute('data-i18n-message',canonical);
            element.textContent=translate(canonical);
        }
        function option(label,value){
            const element=new optionConstructor(translate(label),value);
            element.setAttribute('data-i18n',i18n.translate(label,'en'));
            return element;
        }
        function textParts(element,messages){
            if(!element)return;
            const parts=messages.map(message=>translateTo(message,'en'));
            element.removeAttribute?.('data-i18n');
            element.removeAttribute?.('data-i18n-message');
            element.setAttribute('data-i18n-parts',JSON.stringify(parts));
            element.textContent=parts.map(translate).join(' ');
        }
        function audioLabel(label){
            const match=label.match(/^(Portuguese \(Brazil\)|Portuguese|English|Japanese|Spanish|French|German|Italian)( \([^)]*\).*)$/);
            return match?translate(match[1])+match[2]:label;
        }
        function audioOption(label,value){
            const element=new optionConstructor(audioLabel(label),value);
            element.setAttribute('data-i18n-audio',label);
            return element;
        }
        function refresh(){
            for(const element of document.querySelectorAll('[data-i18n]')) element.textContent=translate(element.getAttribute('data-i18n'));
            for(const [attribute,target] of [['data-i18n-placeholder','placeholder'],['data-i18n-title','title'],['data-i18n-aria','aria-label']]){
                for(const element of document.querySelectorAll('['+attribute+']'))element.setAttribute(target,translate(element.getAttribute(attribute)));
            }
            for(const element of document.querySelectorAll('[data-i18n-audio]'))element.textContent=audioLabel(element.getAttribute('data-i18n-audio'));
            for(const element of document.querySelectorAll('[data-i18n-message]'))element.textContent=translate(element.getAttribute('data-i18n-message'));
            for(const element of document.querySelectorAll('[data-i18n-parts]')){
                try{element.textContent=JSON.parse(element.getAttribute('data-i18n-parts')).map(translate).join(' ');}catch{}
            }
        }
        function apply(){
            const previousLayout=document.documentElement.dataset.layout;
            document.documentElement.dataset.layout=preferences.layout;
            if(previousLayout!==preferences.layout || document.body.dataset.panelOpen===undefined)document.body.dataset.panelOpen=['classic','studio','library'].includes(preferences.layout)?'true':'false';
            document.documentElement.dataset.theme=preferences.theme;
            document.documentElement.lang=preferences.language==='pt'?'pt-BR':preferences.language;
            for(const [id,key] of [['layoutSelect','layout'],['themeSelect','theme'],['uiLanguage','language']]){
                const element=document.getElementById(id);if(element)element.value=preferences[key];
            }
            refresh();
            const toggle=document.getElementById('themeToggle');
            if(toggle){toggle.innerHTML=preferences.theme==='dark'?'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/></svg>':'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M20 15.5A9 9 0 0 1 8.5 4 9 9 0 1 0 20 15.5Z"/></svg>';toggle.setAttribute('aria-label',translate(preferences.theme==='dark'?'Switch to light theme':'Switch to dark theme'));}
            if(onChange)onChange({...preferences});
        }
        function update(changes){
            preferences=normalizePreferences({...preferences,...changes});
            try{storage.setItem(KEY,JSON.stringify(preferences));}catch{/* Preferences still work in memory when browser storage is unavailable. */}
            apply();
        }
        return {init:apply,update,translate,text,textParts,option,audioOption,refresh,getPreferences:()=>({...preferences}),toggleTheme:()=>update({theme:preferences.theme==='dark'?'light':'dark'})};
    }
    const api={normalizePreferences,readPreferences,createController};
    if(typeof module!=='undefined'&&module.exports)module.exports=api;
    else{
        let storage;try{storage=root.localStorage;}catch{storage=null;}
        root.AppUI=createController({document:root.document,storage,i18n:root.PlayerI18n,onChange:()=>root.dispatchEvent(new CustomEvent('streamchrome:preferences'))});
    }
})(typeof window!=='undefined'?window:globalThis);
