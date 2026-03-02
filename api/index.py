from fastapi import FastAPI, HTTPException, Query
from typing import List, Optional
import requests
from bs4 import BeautifulSoup
import re

app = FastAPI(title="NFC-e SP Parser API", version="1.0.0")

def clean_text(text: Optional[str]) -> str:
    if not text:
        return ""
    # Remove multiple spaces, newlines, and strip
    return " ".join(text.split()).strip()

def parse_float(value_str: Optional[str]) -> float:
    if not value_str:
        return 0.0
    try:
        # Encontra o primeiro número na string (evita pegar pontos de rótulos como "Vl. Unit.:")
        match = re.search(r'(\d+[\d.,]*)', value_str)
        if not match:
            return 0.0
        
        val = match.group(1)
        # Se tiver vírgula, tratamos como padrão brasileiro
        if ',' in val:
            val = val.replace('.', '').replace(',', '.')
        return float(val)
    except (ValueError, TypeError):
        return 0.0

def parse_int(value_str: Optional[str]) -> int:
    if not value_str:
        return 0
    try:
        clean_value = re.sub(r'[^\d]', '', value_str)
        return int(clean_value) if clean_value else 0
    except (ValueError, TypeError):
        return 0

@app.get("/")
def health_check():
    return {"status": "ok", "message": "NFC-e Parser API is running"}

@app.get("/parse")
def parse_nfce(url: str = Query(..., description="URL da consulta de NFC-e (SP)")):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=400, detail=f"Erro ao acessar a URL: {str(e)}")

    soup = BeautifulSoup(response.text, "lxml")
    
    # 1. Emitente
    emitente_data = {
        "nome": "",
        "cnpj": "",
        "endereco": ""
    }
    
    # Procurar primeiro dentro de #conteudo para ser mais assertivo
    conteudo = soup.find("div", {"id": "conteudo"})
    emitente_div = (conteudo.find("div", class_="txtCenter") if conteudo else None) or soup.find("div", class_="txtCenter")
    
    if emitente_div:
        nome_span = emitente_div.find("span", class_="txtTit")
        if nome_span:
            emitente_data["nome"] = clean_text(nome_span.text)
            
        inf_text = emitente_div.get_text(separator="\n")
        cnpj_match = re.search(r'CNPJ:\s*([\d./-]+)', inf_text)
        if cnpj_match:
            emitente_data["cnpj"] = cnpj_match.group(1).strip()
            
        lines: List[str] = [l.strip() for l in inf_text.split("\n") if l.strip()]
        # Filtra linhas que não são nome nem CNPJ para compor endereço
        addr_parts = []
        for line in lines:
            if line == emitente_data["nome"] or "CNPJ" in line.upper() or "IE:" in line.upper():
                continue
            addr_parts.append(line)
        if addr_parts:
            emitente_data["endereco"] = ", ".join(addr_parts)

    # 2. Produtos
    itens = []
    table_items = soup.find("table", {"id": "tabResult"})
    if table_items:
        rows = table_items.find_all("tr")
        for row in rows:
            nome_span = row.find("span", class_="txtTit")
            if not nome_span: continue
                
            nome_item = clean_text(nome_span.text)
            codigo_row = row.find("span", class_="RCod")
            codigo = re.sub(r'[^\d]', '', codigo_row.text) if codigo_row else ""
            
            # Limpando rótulos extras das unidades e quantidades
            qtd_text = row.find("span", class_="Rqty").text if row.find("span", class_="Rqty") else ""
            unid_text = row.find("span", class_="RUN").text if row.find("span", class_="RUN") else ""
            
            itens.append({
                "descricao": nome_item,
                "codigo": codigo,
                "quantidade": parse_float(qtd_text),
                "unidade": clean_text(unid_text.replace("UN:", "").replace("Un:", "").strip()),
                "valor_unitario": parse_float(row.find("span", class_="RvalUnit").text if row.find("span", class_="RvalUnit") else ""),
                "valor_total": parse_float(row.find("span", class_="valor").text if row.find("span", class_="valor") else "")
            })

    # 3. Totais
    valor_pagar = 0.0
    tributos = 0.0
    val_total_span = soup.find("span", class_="totalNumb txtMax")
    if val_total_span:
        valor_pagar = parse_float(val_total_span.text)
            
    tributos_label = soup.find(string=re.compile(r'Tributos Totais Incidentes', re.I)) or soup.find("span", class_="txtObs")
    if tributos_label:
        # Pode estar no próximo span ou no texto do pai
        tributos = parse_float(tributos_label.parent.get_text())

    # 4. Dados da Nota
    chave_span = soup.find("span", class_="chave")
    chave = clean_text(chave_span.text) if chave_span else ""
    
    numero = ""
    serie = ""
    data_emissao = ""
    
    info_container = soup.find("div", {"id": "infos"}) or soup.find("ul", class_="list-unstyled")
    if info_container:
        info_text = info_container.get_text(separator=" ")
        num_m = re.search(r'N.mero:\s*(\d+)', info_text, re.I)
        if num_m: numero = num_m.group(1)
        
        ser_m = re.search(r'S.rie:\s*(\d+)', info_text, re.I)
        if ser_m: serie = ser_m.group(1)
        
        dat_m = re.search(r'Emiss.o:\s*(\d{2}/\d{2}/\d{4}\s*\d{2}:\d{2}:\d{2})', info_text, re.I)
        if dat_m: data_emissao = dat_m.group(1)

    # 5. Consumidor
    cpf_consumidor = ""
    # Busca específica no bloco de consumidor se existir
    dest_div = soup.find("div", {"id": "destinatario"}) or soup
    cpf_match = re.search(r'CPF:\s*([\d.*-]+)', dest_div.get_text(), re.I)
    if cpf_match:
        cpf_consumidor = cpf_match.group(1).strip()

    return {
        "emitente": emitente_data,
        "itens": itens,
        "totais": {
            "valor_pagar": valor_pagar,
            "quantidade_itens": len(itens),
            "tributos_estimados": tributos
        },
        "chave_acesso": chave,
        "dados_nota": {
            "numero": numero,
            "serie": serie,
            "data_emissao": data_emissao
        },
        "consumidor": {
            "cpf": cpf_consumidor
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
