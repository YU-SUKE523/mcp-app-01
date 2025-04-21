import asyncio
import boto3
import json
import uuid
import time
import streamlit as st
from datetime import datetime, timedelta
from langchain_aws import ChatBedrockConverse
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from langchain_mcp_adapters.client import MultiServerMCPClient

# AWS クライアント初期化
bedrock_client = boto3.client('bedrock-runtime', region_name="us-west-2")
dynamodb = boto3.resource('dynamodb', region_name="ap-northeast-1")
CONVERSATION_TABLE = "sample-conversation-history"  # テーブル名を設定

# 会話履歴を保存する関数
def save_conversation(conversation_id, messages):
    table = dynamodb.Table(CONVERSATION_TABLE)
    
    # 既存のメッセージを削除
    table.delete_item(
        Key={
            'conversation_id': conversation_id,
            'timestamp': 'latest'
        }
    )
    
    # メッセージの内容をシリアライズ可能な形式に変換
    serializable_messages = []
    for msg in messages:
        if isinstance(msg.content, str):
            content = msg.content
        else:
            content = json.dumps(msg.content)
        
        serializable_messages.append({
            'type': msg.type,
            'content': content
        })
    
    # TTLを30日後に設定
    expiration_time = int(time.time() + 30 * 24 * 60 * 60)
    
    # 最新の会話を保存
    table.put_item(
        Item={
            'conversation_id': conversation_id,
            'timestamp': 'latest',
            'last_updated': datetime.now().isoformat(),
            'messages': serializable_messages,
            'expiration_time': expiration_time
        }
    )
    
    # 時系列での履歴も保存（履歴追跡のため）
    table.put_item(
        Item={
            'conversation_id': conversation_id,
            'timestamp': datetime.now().isoformat(),
            'messages': serializable_messages,
            'expiration_time': expiration_time
        }
    )

# 会話履歴を読み込む関数
def load_conversation(conversation_id):
    table = dynamodb.Table(CONVERSATION_TABLE)
    
    try:
        response = table.get_item(
            Key={
                'conversation_id': conversation_id,
                'timestamp': 'latest'
            }
        )
        
        if 'Item' not in response:
            return []
        
        item = response['Item']
        messages = []
        
        for msg_data in item['messages']:
            if msg_data['type'] == 'human':
                messages.append(HumanMessage(content=msg_data['content']))
            elif msg_data['type'] == 'ai':
                messages.append(AIMessage(content=msg_data['content']))
        
        return messages
    except Exception as e:
        st.error(f"会話の読み込みに失敗しました: {str(e)}")
        return []

# 会話一覧を取得する関数
def list_conversations():
    table = dynamodb.Table(CONVERSATION_TABLE)
    
    try:
        # 'latest'タイムスタンプを持つ項目だけをクエリ
        response = table.scan(
            FilterExpression="(#ts = :latest)",
            ExpressionAttributeNames={
                '#ts': 'timestamp'
            },
            ExpressionAttributeValues={
                ':latest': 'latest'
            }
        )
        
        conversations = []
        for item in response.get('Items', []):
            # 最終更新日時があれば取得
            last_updated = item.get('last_updated', 'Unknown')
            if last_updated != 'Unknown':
                try:
                    last_updated = datetime.fromisoformat(last_updated).strftime('%Y-%m-%d %H:%M')
                except:
                    pass
            
            conversations.append({
                'id': item['conversation_id'],
                'last_updated': last_updated
            })
        
        # 最終更新日時でソート
        conversations.sort(key=lambda x: x['last_updated'], reverse=True)
        return conversations
    except Exception as e:
        st.error(f"会話一覧の取得に失敗しました: {str(e)}")
        return []

async def main():
    st.title("Bedrock chat with MCP tools")
    
    # サイドバーに会話管理UIを追加
    with st.sidebar:
        st.header("会話管理")
        if st.button("新しい会話を開始"):
            st.session_state.conversation_id = str(uuid.uuid4())
            st.session_state.messages = []
            st.rerun()
        
        st.subheader("保存された会話")
        conversations = list_conversations()
        
        if conversations:
            conversation_options = {f"{conv['id']} (更新: {conv['last_updated']})": conv['id'] for conv in conversations}
            selected_conversation_label = st.selectbox(
                "会話を選択", 
                options=list(conversation_options.keys()),
                index=None
            )
            
            if selected_conversation_label and st.button("会話を読み込む"):
                selected_id = conversation_options[selected_conversation_label]
                st.session_state.conversation_id = selected_id
                st.session_state.messages = load_conversation(selected_id)
                st.rerun()
    
    # 会話IDが未設定の場合は新規作成
    if "conversation_id" not in st.session_state:
        st.session_state.conversation_id = str(uuid.uuid4())
    
    # チャット履歴の保存場所を初期化
    if "messages" not in st.session_state:
        st.session_state.messages = []
    messages = st.session_state.messages
    
    # 現在の会話IDを表示
    st.info(f"現在の会話ID: {st.session_state.conversation_id}")
    
    # メッセージ表示部分
    printable_messages = [
        message for message in messages if message.type in ["ai", "human"]
    ]

    for message in printable_messages:
        # 文字列の場合の処理
        if isinstance(message.content, str):
            with st.chat_message(message.type):
                st.write(message.content)
        # リスト形式のメッセージ（複数形式コンテンツ）の処理
        elif isinstance(message.content, list):
            for content in message.content:
                if content["type"] == "text":
                    with st.chat_message(message.type):
                        st.write(content["text"])

    if prompt := st.chat_input():  # ユーザーがメッセージを入力したら
        with st.chat_message("human"):
            st.write(prompt)  # 画面にユーザーの入力を表示
            
        messages.append(HumanMessage(prompt))  # 履歴にユーザーメッセージを追加

        chat_model = ChatBedrockConverse(
            model="us.anthropic.claude-3-7-sonnet-20250219-v1:0",client=bedrock_client
        )
        
        try:
            with open("mcp_config.json", "r") as f:
                config = json.load(f)

            async with MultiServerMCPClient(config["mcpServers"]) as mcp_client:
                try:
                    tools = mcp_client.get_tools()
                    
                    print(tools)
                    
                    while True:
                        try:
                            ai_response = await chat_model.bind_tools(tools).ainvoke(messages)
                            
                            print(f"ai_response:{ai_response}")

                            messages.append(ai_response)

                            if isinstance(ai_response.content, str):
                                print("STRです")
                                with st.chat_message("ai"):
                                    st.write(ai_response.content)
                            elif isinstance(ai_response.content, list):
                                print("Listです")
                                for content in ai_response.content:
                                    if content["type"] == "text":
                                        with st.chat_message("ai"):
                                            st.write(content["text"])

                            if ai_response.tool_calls:
                                for tool_call in ai_response.tool_calls:
                                    status = st.status(
                                        f"Tool Call: {tool_call['name']}", expanded=True
                                    )
                                    status.write(tool_call['args'])
                                    
                                    try:
                                        # ツール名の検索とエラーハンドリング
                                        tool_name = tool_call["name"].lower()
                                        if tool_name not in {tool.name.lower(): tool for tool in tools}:
                                            error_msg = f"ツール '{tool_call['name']}' が見つかりません。"
                                            status.update(label=f"Error: {tool_call['name']}", state="error")
                                            status.write(error_msg)
                                            tool_msg = ToolMessage(
                                                name=tool_call["name"],
                                                content=f"エラー: {error_msg}",
                                                tool_call_id=tool_call.get("id", "unknown")
                                            )
                                        else:
                                            selected_tool = {tool.name.lower(): tool for tool in tools}[tool_name]
                                            tool_msg = await selected_tool.ainvoke(tool_call)
                                            status.update(state="complete")
                                        
                                        print(f"tool_msg:{tool_msg}")
                                        messages.append(tool_msg)
                                    except Exception as e:
                                        error_msg = f"ツール実行中にエラーが発生しました: {str(e)}"
                                        status.update(label=f"Error: {tool_call['name']}", state="error")
                                        status.write(error_msg)
                                        # エラーメッセージをツールの応答として追加
                                        tool_msg = ToolMessage(
                                            name=tool_call["name"],
                                            content=f"エラー: {error_msg}",
                                            tool_call_id=tool_call.get("id", "unknown")
                                        )
                                        messages.append(tool_msg)
                            else:
                                break
                        except Exception as e:
                            error_message = f"AIレスポンス処理中にエラーが発生しました: {str(e)}"
                            st.error(error_message)
                            # エラーメッセージをAIメッセージとして追加
                            messages.append(AIMessage(content=f"処理エラー: {error_message}"))
                            break
                except Exception as e:
                    error_message = f"ツール取得中にエラーが発生しました: {str(e)}"
                    st.error(error_message)
                    # エラーメッセージをAIメッセージとして追加
                    messages.append(AIMessage(content=error_message))
                    with st.chat_message("ai"):
                        st.write("申し訳ございません。ツールの準備中にエラーが発生しました。別の質問をお試しください。")
        except Exception as e:
            error_message = f"MCP接続中にエラーが発生しました: {str(e)}"
            st.error(error_message)
            messages.append(AIMessage(content=error_message))
            with st.chat_message("ai"):
                st.write("申し訳ございません。システムとの接続中にエラーが発生しました。しばらくしてからもう一度お試しください。")
        
        # 会話終了後に履歴を保存
        save_conversation(st.session_state.conversation_id, messages)

asyncio.run(main())