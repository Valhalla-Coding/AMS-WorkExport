import os


def clr():
    os.system("cls" if os.name == "nt" else "clear")


def hr(char="─", width=72):
    print(char * width)


def pause(msg="Tryck Enter för att gå tillbaka till menyn..."):
    input(f"\n{msg}")


def print_row(title: str, city: str, min_str: str, status: str,
              counter: str = ""):
    t   = (title[:33] + "..") if len(title) > 35 else title
    c   = (city[:13]  + "..") if len(city)  > 15 else city
    ctr = f"{counter:<8}" if counter else "        "
    print(f"  {ctr}{t:<35}  {c:<15}  {min_str:>6}  {status}")
