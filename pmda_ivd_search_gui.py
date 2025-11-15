import datetime
import socket
import traceback
import tkinter as tk
from http.client import RemoteDisconnected
from tkinter import ttk, messagebox

import chromedriver_autoinstaller
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait
from urllib3.exceptions import ProtocolError

PMDA_IVD_URL = "https://www.pmda.go.jp/PmdaSearch/ivdSearch/"
PMDA_MEDICAL_URL = "https://www.pmda.go.jp/PmdaSearch/iyakuSearch/"
PMDA_BASE_URL = "https://www.pmda.go.jp/"


def has_internet_connection(timeout: float = 5.0) -> bool:
    try:
        with socket.create_connection(("www.pmda.go.jp", 443), timeout=timeout):
            pass
        return True
    except OSError:
        return False


def create_chrome_driver() -> webdriver.Chrome:
    chromedriver_path = chromedriver_autoinstaller.install()
    options = webdriver.ChromeOptions()
    # options.add_argument("--headless=new")  # 必要ならヘッドレス
    service = ChromeService(executable_path=chromedriver_path)
    return webdriver.Chrome(service=service, options=options)


def run_pmda_ivd_search(keyword: str, drug_type: str, use_date: bool, from_date: str, to_date: str) -> None:
    keyword = keyword.strip()
    if not keyword:
        messagebox.showwarning("入力不足", "キーワードを入力してください。")
        return

    use_date = bool(use_date)
    from_date = (from_date or "").strip()
    to_date = (to_date or "").strip()

    # 日付チェック
    if use_date:
        for label, v in [("開始日", from_date), ("終了日", to_date)]:
            if not v:
                messagebox.showwarning("入力不足", f"{label}を入力してください（YYYY/MM/DD）")
                return
            try:
                datetime.datetime.strptime(v, "%Y/%m/%d")
            except ValueError:
                messagebox.showwarning("日付形式エラー", f"{label}は YYYY/MM/DD 形式で入力してください。")
                return

    if not has_internet_connection():
        messagebox.showerror("通信エラー", "インターネットに接続されていません。検索を終了します。")
        return

    driver = create_chrome_driver()
    wait = WebDriverWait(driver, 20)

    try:
        driver.get(PMDA_IVD_URL)

        # ページ全体のロードを待つ
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))

        # 1) 画面上のテキスト入力欄を全部拾う
        text_boxes = wait.until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "input[type='text']"))
        )
        if len(text_boxes) == 0:
            raise NoSuchElementException("テキスト入力欄 (input[type='text']) が見つかりません。PMDAの画面仕様が変わった可能性があります。")

        # 想定: 0番目 = 一般的名称・販売名
        name_input = text_boxes[0]
        name_input.clear()
        name_input.send_keys(keyword)

        # 想定: 更新年月日で検索 の from/to がこのあとに続く 2つのテキストボックス
        if use_date and len(text_boxes) >= 3:
            from_input = text_boxes[1]
            to_input = text_boxes[2]
            from_input.clear()
            from_input.send_keys(from_date)
            to_input.clear()
            to_input.send_keys(to_date)
        elif use_date:
            # 日付フィールドが見つからない場合は警告だけ出して続行
            messagebox.showwarning(
                "注意",
                "更新年月日の入力欄が見つからなかったため、日付条件なしで検索します。"
            )

        # 2) プルダウン(select)を全部拾う
        selects = driver.find_elements(By.TAG_NAME, "select")
        if len(selects) > 0:
            # 想定: 1番目の select が「薬効分類（医薬品の種類）」
            # 体外診断用医薬品 or 体外診断用医薬品（放射性）を選択する
            try:
                drug_type_select = Select(selects[0])
                if drug_type == "ivd_only":
                    # 「体外診断用医薬品」を含む option を選択
                    for opt in drug_type_select.options:
                        if "体外診断用医薬品" in opt.text and "放射性" not in opt.text:
                            opt.click()
                            break
                elif drug_type == "ivd_radio":
                    for opt in drug_type_select.options:
                        if "体外診断用医薬品（放射性" in opt.text:
                            opt.click()
                            break
                # "all" の場合はデフォルトのまま
            except Exception:
                # プルダウンの構造が違っても、とりあえず検索は継続
                traceback.print_exc()
        else:
            # select が見つからなくても致命的ではないので警告のみにする
            print("警告: <select> 要素が見つかりませんでした。薬効分類の絞り込みは行われません。")

        # 3) 「検索」ボタンを探してクリック
        # 画面上の type=submit or name/value に「検索」を含むボタンを手当たり次第探す
        search_clicked = False

        # input type=submit / button など
        for btn in driver.find_elements(By.TAG_NAME, "input"):
            try:
                t = (btn.get_attribute("value") or "") + (btn.text or "")
                if "検索" in t:
                    btn.click()
                    search_clicked = True
                    break
            except Exception:
                continue

        if not search_clicked:
            for btn in driver.find_elements(By.TAG_NAME, "button"):
                try:
                    if "検索" in (btn.text or ""):
                        btn.click()
                        search_clicked = True
                        break
                except Exception:
                    continue

        if not search_clicked:
            raise NoSuchElementException("「検索」ボタンが見つかりませんでした。")

        # 4) 検索結果テーブルを待つ（かなり緩く見る）
        # 想定: 検索結果一覧が table 要素で表示される
        try:
            result_table = wait.until(
                EC.presence_of_element_located((By.TAG_NAME, "table"))
            )
            driver.maximize_window()
            # 検索結果画面をそのままユーザーに見せるだけなので、ここで return
        except TimeoutException:
            # テーブルが出なくてもブラウザ側で結果を確認できるケースがあるため、通知は控える
            print("IVD: 検索結果テーブルの検出に失敗しました。ブラウザでご確認ください。")
        except (RemoteDisconnected, ProtocolError, WebDriverException):
            # ブラウザがユーザー操作などで閉じられたケースを想定
            messagebox.showinfo(
                "情報",
                "ブラウザとの接続が切断されたため、検索を終了しました。"
            )

    except Exception as e:
        traceback.print_exc()
        messagebox.showerror(
            "エラー",
            f"検索の実行中にエラーが発生しました:\n{type(e).__name__}: {e}"
        )
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def run_pmda_medical_search(keyword: str, use_date: bool, from_date: str, to_date: str) -> None:
    keyword = keyword.strip()
    if not keyword:
        messagebox.showwarning("入力不足", "キーワードを入力してください。")
        return

    use_date = bool(use_date)
    from_date = (from_date or "").strip()
    to_date = (to_date or "").strip()

    if use_date:
        for label, v in [("開始日", from_date), ("終了日", to_date)]:
            if not v:
                messagebox.showwarning("入力不足", f"{label}を入力してください（YYYY/MM/DD）")
                return
            try:
                datetime.datetime.strptime(v, "%Y/%m/%d")
            except ValueError:
                messagebox.showwarning("日付形式エラー", f"{label}は YYYY/MM/DD 形式で入力してください。")
                return

    if not has_internet_connection():
        messagebox.showerror("通信エラー", "インターネットに接続されていません。検索を終了します。")
        return

    driver = create_chrome_driver()
    wait = WebDriverWait(driver, 20)

    try:
        driver.get(PMDA_MEDICAL_URL)
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))

        text_boxes = wait.until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, "input[type='text']"))
        )
        if len(text_boxes) == 0:
            raise NoSuchElementException("医療用医薬品検索の入力欄が見つかりません。")

        name_input = text_boxes[0]
        name_input.clear()
        name_input.send_keys(keyword)

        if use_date and len(text_boxes) >= 3:
            from_input = text_boxes[1]
            to_input = text_boxes[2]
            from_input.clear()
            from_input.send_keys(from_date)
            to_input.clear()
            to_input.send_keys(to_date)
        elif use_date:
            messagebox.showwarning(
                "注意",
                "更新年月日の入力欄が見つからなかったため、日付条件なしで検索します。"
            )

        search_clicked = False
        for btn in driver.find_elements(By.TAG_NAME, "input"):
            try:
                t = (btn.get_attribute("value") or "") + (btn.text or "")
                if "検索" in t:
                    btn.click()
                    search_clicked = True
                    break
            except Exception:
                continue

        if not search_clicked:
            for btn in driver.find_elements(By.TAG_NAME, "button"):
                try:
                    if "検索" in (btn.text or ""):
                        btn.click()
                        search_clicked = True
                        break
                except Exception:
                    continue

        if not search_clicked:
            raise NoSuchElementException("医療用医薬品検索の「検索」ボタンが見つかりませんでした。")

        try:
            wait.until(EC.presence_of_element_located((By.TAG_NAME, "table")))
            driver.maximize_window()
        except TimeoutException:
            print("医療用医薬品: 検索結果テーブルの検出に失敗しました。ブラウザでご確認ください。")
        except (RemoteDisconnected, ProtocolError, WebDriverException):
            messagebox.showinfo(
                "情報",
                "ブラウザとの接続が切断されたため、検索を終了しました。"
            )

    except Exception as e:
        traceback.print_exc()
        messagebox.showerror(
            "エラー",
            f"検索の実行中にエラーが発生しました:\n{type(e).__name__}: {e}"
        )
    finally:
        try:
            driver.quit()
        except Exception:
            pass


class PmdaIvdSearchApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("PMDA 体外診断用医薬品検索")

        self.category_var = tk.StringVar(value="ivd")
        self.keyword_var = tk.StringVar()
        self.drug_type_var = tk.StringVar(value="all")
        self.use_date_var = tk.BooleanVar(value=False)
        self.from_date_var = tk.StringVar()
        self.to_date_var = tk.StringVar()

        self._build_ui()

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.root, padding=20)
        frame.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        ttk.Label(frame, text="検索対象").grid(row=0, column=0, sticky="w")
        target_frame = ttk.Frame(frame)
        target_frame.grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Radiobutton(
            target_frame,
            text="体外診断用医薬品 (IVD)",
            variable=self.category_var,
            value="ivd",
            command=self._on_category_change,
        ).pack(side="left")
        ttk.Radiobutton(
            target_frame,
            text="医療用医薬品",
            variable=self.category_var,
            value="medical",
            command=self._on_category_change,
        ).pack(side="left", padx=(10, 0))

        ttk.Label(frame, text="一般的名称・販売名").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.keyword_entry = ttk.Entry(frame, textvariable=self.keyword_var, width=40)
        self.keyword_entry.grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=(10, 0))
        self.keyword_entry.focus()

        ttk.Label(frame, text="薬効分類 (IVD のみ)").grid(row=2, column=0, sticky="w", pady=(10, 0))
        self.drug_type_combo = ttk.Combobox(
            frame,
            state="readonly",
            textvariable=self.drug_type_var,
            values=(
                "all",
                "ivd_only",
                "ivd_radio",
            ),
            width=37,
        )
        self.drug_type_combo.grid(row=2, column=1, sticky="w", padx=(10, 0), pady=(10, 0))
        self.drug_type_combo.set("all")
        ttk.Label(
            frame,
            text="all: すべて, ivd_only: 体外診断用医薬品, ivd_radio: 体外診断用医薬品（放射性）",
            foreground="#555",
        ).grid(row=3, column=0, columnspan=2, sticky="w")

        ttk.Checkbutton(
            frame,
            text="更新年月日で検索",
            variable=self.use_date_var,
            command=self._toggle_date_fields,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(15, 0))

        date_frame = ttk.Frame(frame)
        date_frame.grid(row=5, column=0, columnspan=2, sticky="w", pady=(5, 0))

        ttk.Label(date_frame, text="開始日 (YYYY/MM/DD)").grid(row=0, column=0, sticky="w")
        self.from_entry = ttk.Entry(date_frame, textvariable=self.from_date_var, width=20, state="disabled")
        self.from_entry.grid(row=0, column=1, sticky="w", padx=(5, 15))

        ttk.Label(date_frame, text="終了日 (YYYY/MM/DD)").grid(row=0, column=2, sticky="w")
        self.to_entry = ttk.Entry(date_frame, textvariable=self.to_date_var, width=20, state="disabled")
        self.to_entry.grid(row=0, column=3, sticky="w", padx=(5, 0))

        button_frame = ttk.Frame(frame)
        button_frame.grid(row=6, column=0, columnspan=2, pady=(20, 0), sticky="ew")
        ttk.Button(button_frame, text="検索開始", command=self._run_search).pack(side="left", ipadx=10, ipady=5)
        ttk.Button(button_frame, text="条件クリア", command=self._clear_fields).pack(side="left", padx=(10, 0), ipadx=8, ipady=5)
        ttk.Button(button_frame, text="終了", command=self.root.destroy).pack(side="right", ipadx=10, ipady=5)

        self._on_category_change()

    def _toggle_date_fields(self) -> None:
        state = "normal" if self.use_date_var.get() else "disabled"
        self.from_entry.configure(state=state)
        self.to_entry.configure(state=state)

    def _on_category_change(self) -> None:
        if self.category_var.get() == "ivd":
            self.drug_type_combo.configure(state="readonly")
        else:
            self.drug_type_combo.set("all")
            self.drug_type_combo.configure(state="disabled")

    def _run_search(self) -> None:
        if self.category_var.get() == "ivd":
            run_pmda_ivd_search(
                keyword=self.keyword_var.get(),
                drug_type=self.drug_type_var.get(),
                use_date=self.use_date_var.get(),
                from_date=self.from_date_var.get(),
                to_date=self.to_date_var.get(),
            )
        else:
            run_pmda_medical_search(
                keyword=self.keyword_var.get(),
                use_date=self.use_date_var.get(),
                from_date=self.from_date_var.get(),
                to_date=self.to_date_var.get(),
            )

    def _clear_fields(self) -> None:
        self.keyword_var.set("")
        self.drug_type_var.set("all")
        self.use_date_var.set(False)
        self.from_date_var.set("")
        self.to_date_var.set("")
        self._toggle_date_fields()
        self.keyword_entry.focus_set()


def main() -> None:
    root = tk.Tk()
    app = PmdaIvdSearchApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
