#include <iostream>
#include <fstream>
#include <sstream>
#include <string>
#include <windows.h>
#include <vector>

using namespace std;

class Date;
class Address;
class Contact;
class StudentContact;
class TeacherContact;

///==================== ContactBook 类：集成所有辅助功能的最终类大王 ===================
class ContactBook {
private:
    vector<Contact*> Data;

public:
    void NewLoad();
    int HowManyPersonData();
    int FindEmptyPos();
    void InformationLoad();
    void AddPersonByHand();
    int AddPersonList();
    int SortList();
    int ThirdList(int Idx);
    int SecondList(int idx);
    int Search(int c);
    int FirstList();
    void BirthRemindList();
    void InAndOut();
    void Statistics();
    void quickSort(int l, int r);
    void quickSortById(int l, int r);
    ~ContactBook();
};

//=================== 清除缓冲区函数 ==================

int CleanBuffer() {
    cin.ignore(10000, '\n');
    return 0;
}


///==================== Date 类：生日 ====================
class Date {
private:
    int year;
    int month;
    int day;
public:
    Date(int y = 2000, int m = 1, int d = 1) {
        year = y;
        month = m;
        day = d;
    }
    
    bool operator==(const Date& target) const{
    	if (day == target.day){
    		if (month == target.month){
    			if (year == target.year){
    				return true;
				}
			}
		}
		return false;
	}
    void show() const {
        cout << year << "-" << month << "-" << day;
    }
    
    void set(int y,int m,int d) {
        year = y;
        month = m;
        day = d;
    }
    void addone(){
    	day++;
    	if (isValid(year,month,day) == false){
    		day = 1;
    		month++;
    		if (isValid(year,month,day) == false){
    			month = 1;
    			year++;
			}
		}
	}
	int getDay() const { return day; }
    int getMonth() const { return month; }
    int getYear() const { return year; }
    int getOrdinalNum() const {

        int ordNum = 0,febDays = 28;

        if ((year % 4 == 0 && year % 100 != 0) || (year % 400 == 0)) febDays = 29;

        int daysInMonth[13] = { 0, 31, febDays, 31, 30, 31, 30,
                                   31, 31, 30, 31, 30, 31 };
        for (int i = 1; i < month;i ++){

            ordNum = ordNum + daysInMonth[i];

        }

        ordNum = ordNum + day;

        return ordNum;

    }

    bool isLeapYear() const {
        if ((year % 4 == 0 && year % 100 != 0) || (year % 400 == 0)) return true;
        else return false;
    }
    static bool isValid(const int& y,const int& m,const int& d) {
        if (y < 0 || y > 3000) return false;
        if (m < 1 || m > 12) return false;
        if (d < 1) return false;
            
        int febDays;
        if ((y % 4 == 0 && y % 100 != 0) || (y % 400 == 0)) febDays = 29;
        else febDays = 28;

        int daysInMonth[13] = { 0, 31, febDays, 31, 30, 31, 30,
                                   31, 31, 30, 31, 30, 31 };

        if (d > daysInMonth[m]) return false;
        else return true;
    }
};

///==================== Address 类：地址 ====================
class Address {
private:
    string city;
    string detail;
public:
    Address(string c = "", string d = "") {
        city = c;
        detail = d;
    }

    void show() const {
        cout << city << " " << detail;
    }

    void setCity(const string& c) { city = c; }
    void setDetail(const string& d) { detail = d; }
    string getCity() const { return city; }
    string getDetail() const { return detail; }
};

///==================== Contact 基类 ====================
class Contact {
private:
    int id;
    string name;
    string telephone;
    Date birthday;
    Address addr;
    bool valid;

    static int nextId;

public:
    Contact() {
        id = 0;
        name = "";
        telephone = "";
        birthday = Date();
        addr = Address();
        valid = false;
    }

    Contact(string n, string t, Date b, Address a) {
        id = nextId++;
        name = n;
        telephone = t;
        birthday = b;
        addr = a;
        valid = true;
    }

    Contact(const Contact& other) {
        id = other.id;
        name = other.name;
        telephone = other.telephone;
        birthday = other.birthday;
        addr = other.addr;
        valid = other.valid;
    }

    virtual ~Contact() {}

    virtual void show() const {
        cout << "编号：" << id << endl;
        cout << "姓名：" << name << endl;
        cout << "电话：" << telephone << endl;
        cout << "生日：";
        birthday.show();
        cout << endl;
        cout << "地址：";
        addr.show();
        cout << endl;
    }

    virtual string getType() const { return "普通"; }

    virtual void setDescend( int& choice, const string& input) {}

    string getName() const { return name; }
    string getTelephone() const { return telephone; }
    bool isFindName(const string& key) const {
    	int u = 0;
    	u = name.find(key);
		if (u == -1){ return false; }
		else { return true; }
	}
	bool isFindTelephone(const string& key) const {
    	int u = 0;
    	u = telephone.find(key);
		if (u == -1){ return false; }
		else { return true; }
	}
    bool isValid() const { return valid; }
    int getId() const { return id; }
    void setId(int i) { id = i; }

    void setName(const string& n) { name = n; }
    void setTelephone(const string& t) { telephone = t; }
    void setAddressCity(const string& c) { addr.setCity(c); }
    void setAddressDetail(const string& d) { addr.setDetail(d); }
    void setBirthday(const int& year,const int& month,const int& day) { this->birthday.set(year,month,day); }
    void showBirthday() const { birthday.show(); }
    Address getAddress() const { return addr; }
    Date getBirthday() const { return birthday; }

    void clear() {
        id = 0;
        name = "";
        telephone = "";
        birthday = Date();
        addr = Address();
        valid = false;
    }

    static int getNextId() { return nextId; }
    static void setNextId(int n) { nextId = n; }
};

int Contact::nextId = 1;

///==================== StudentContact 派生类 ====================
class StudentContact : public Contact {
private:
    string studentId;
    string school;
    string major;
public:
    StudentContact() : Contact() {
        studentId = "";
        school = "";
        major = "";
    }

    StudentContact(string n, string t, Date b, Address a,
                   string sid, string sch, string maj)
        : Contact(n, t, b, a), studentId(sid), school(sch), major(maj) {}

    void show() const override {
        Contact::show();
        cout << "学号：" << studentId << endl;
        cout << "学校：" << school << endl;
        cout << "专业：" << major << endl;
    }
    
    void setDescend(int& choice,const string& input) override {
        if (choice == 6) studentId = input;
        if (choice == 7) school = input;
        if (choice == 8) major = input;
    }

    string getType() const override { return "学生"; }

    string getStudentId() const { return studentId; }
    string getSchool() const { return school; }
    string getMajor() const { return major; }

    void setStudentId(const string& s) { studentId = s; }
    void setSchool(const string& s) { school = s; }
    void setMajor(const string& s) { major = s; }
};

///==================== TeacherContact 派生类 ====================
class TeacherContact : public Contact {
private:
    string teacherId;
    string school;
    string department;
    string title;
public:
    TeacherContact() : Contact() {
        teacherId = "";
        school = "";
        department = "";
        title = "";
    }

    TeacherContact(string n, string t, Date b, Address a,
                   string tid, string sch, string dept, string tit)
        : Contact(n, t, b, a), teacherId(tid), school(sch), department(dept), title(tit) {}

    void show() const override {
        Contact::show();
        cout << "工号：" << teacherId << endl;
        cout << "学校：" << school << endl;
        cout << "系部：" << department << endl;
        cout << "职称：" << title << endl;
    }

    void setDescend( int& choice, const string& input) override {
        if (choice == 6) teacherId = input;
        if (choice == 7) school = input;
        if (choice == 8) department = input;
        if (choice == 9) title = input;
    }

    string getType() const override { return "教师"; }

    string getTeacherId() const { return teacherId; }
    string getSchool() const { return school; }
    string getDepartment() const { return department; }
    string getTitle() const { return title; }

    void setTeacherId(const string& s) { teacherId = s; }
    void setSchool(const string& s) { school = s; }
    void setDepartment(const string& s) { department = s; }
    void setTitle(const string& s) { title = s; }
};

///==================== ContactBook 成员函数 ====================

ContactBook::~ContactBook() {
    for (int i = 0; i < Data.size(); i++) {
        if (Data[i] != nullptr) {
            delete Data[i];
        }
    }
}

void ContactBook::NewLoad() {
    int n = 0;
    const int MaxLimit = 100000;
    cout << "在开始之前，需要先确定联系人列表上限(最大" << MaxLimit << ")：";
    while(!(cin >> n) || n <= 0 || n > MaxLimit){
        cin.clear();
        CleanBuffer();
        if (n > MaxLimit)
            cout << "输入数值超出上限，请输入1~" << MaxLimit << "之间的正整数：";
        else
            cout << "输入不合法，请输入一个正整数：";
    }
    Data.resize(n, nullptr);
    cout << "\n";
    CleanBuffer();
}

int ContactBook::HowManyPersonData() {
    int count = 0;
    for (int i = 0; i < Data.size(); i++) {
        if (Data[i] != nullptr) {
            count++;
        }
    }
    return count;
}

int ContactBook::FindEmptyPos() {
    for (int i = 0; i < Data.size(); i++) {
        if (Data[i] == nullptr) {
            return i;
        }
    }
    return -1;
}

void ContactBook::InformationLoad() {
    if (Data.size() >= 1) {
        Data[0] = new Contact("测试联系人1", "13795264851", Date(2005, 3, 12),
                              Address("厦门", "厦门理工学院三期宿舍1号楼101"));
    }
    if (Data.size() >= 2) {
        Data[1] = new StudentContact("测试联系人2", "13677989172", Date(2004, 10, 8),
                                     Address("泉州", "华厦学院宿舍1号楼203"),
                                     "2021001", "华厦学院", "计算机科学");
    }
    if (Data.size() >= 3) {
        Data[2] = new TeacherContact("测试联系人3", "14890350715", Date(2003, 6, 20),
                                     Address("龙岩", "龙岩二中启智楼401"),
                                     "T2020001", "龙岩二中", "数学系", "教授");
    }
}

///==================== 排序菜单 ====================
int ContactBook::SortList() {
    int choice = 1;
    while (choice != 0) {
        cout << "\n------------排序联系人------------\n";
        cout << "\n1.按姓名排序";
        cout << "\n2.按编号排序";
        cout << "\n0.返回上一步\n";
        cout << "\nMake your choice :";

        if ((cin >> choice) && choice >= 0 && choice <= 2) {
            CleanBuffer();
            if (choice == 0) return 0;
            if (HowManyPersonData() == 0) {
                cout << "\n*当前通讯录为空，无需排序。\n";
                continue;
            }

            // nullptr 沉底
            int L = 0, R = Data.size() - 1;
            while (L < R){
                while (L < R && Data[L] != nullptr) L++;
                while (L < R && Data[R] == nullptr) R--;
                if (L < R) swap(Data[L++], Data[R--]);
            }
            int validEnd = Data.size() - 1;
            while (validEnd >= 0 && Data[validEnd] == nullptr) validEnd--;

            cout << "\n*排序中......\n";
            if (validEnd > 0){
                if (choice == 1)
                    quickSort(0, validEnd);
                else
                    quickSortById(0, validEnd);
            }
            cout << "*排序完成!\n";
        }
        else {
            cin.clear();
            CleanBuffer();
            cout << "\n*不是一个有效的数字!\n\n";
            choice = 1;
        }
    }
    return 0;
}

void ContactBook::quickSort(int l, int r){
    if(l >= r) return;
    int x = (l + r) / 2;
    int i = l - 1, j = r + 1;
    string xName = Data[x]->getName();
    while(i < j){
        do i ++; while(Data[i]->getName() < xName);
        do j --; while(Data[j]->getName() > xName);
        if(i < j) swap(Data[i], Data[j]);
    }
    quickSort(l, j);
    quickSort(j + 1, r);
}

void ContactBook::quickSortById(int l, int r){
    if(l >= r) return;
    int x = (l + r) / 2;
    int i = l - 1, j = r + 1;
    int xId = Data[x]->getId();
    while(i < j){
        do i ++; while(Data[i]->getId() < xId);
        do j --; while(Data[j]->getId() > xId);
        if(i < j) swap(Data[i], Data[j]);
    }
    quickSortById(l, j);
    quickSortById(j + 1, r);
}

///==================== 手动添加联系人（含类型选择）====================
void ContactBook::AddPersonByHand() {
    int pos = FindEmptyPos();
    if (pos == -1) {
        cout << "*联系人数量超过上限了......\n";
        return;
    }

    cout << "\n请选择联系人类型:";
    cout << "\n1.普通联系人";
    cout << "\n2.学生联系人";
    cout << "\n3.教师联系人";
    cout << "\n0.取消添加\n";
    cout << "Make your choice :";

    int typeChoice = 0;
    if (!(cin >> typeChoice) || typeChoice < 0 || typeChoice > 3) {
        cin.clear();
        CleanBuffer();
        cout << "*无效选择，已取消！\n";
        return;
    }
    CleanBuffer();

    if (typeChoice == 0) {
        cout << "*已取消添加！\n";
        return;
    }

    string name;
    string telephone;
    string city;
    string detail;
    int y, m, d;

    cout << "\n如果不希望添加联系人信息，请直接键入回车\n";
    cout << "请输入联系人姓名:";
    getline(cin, name);

    if (name.empty()) {
        cout << "*已取消添加！\n";
        return;
    }

    cout << "请输入联系人电话:";
    while (getline(cin, telephone)){
    	if (telephone.size() == 11){
    		if (telephone[0] != '1'){
    			cout << "不合法的电话号码，请重新输入：" ;
			}
            else {
                break;
            }
		}
		else {
			cout << "您输入的是一个非常用的电话号码，确认要继续吗？" << endl;
			cout << "1.重新输入" << endl;
			cout << "2.确认继续" << endl;
			cout << "Make your choice :";
			int ch = 0;
			while (!(cin >> ch) || ((ch < 1) || (ch > 2)) ){
				cin.clear();
				CleanBuffer();
				cout << "不合法的选择，请重新输入：";
			}
			CleanBuffer();
			if (ch == 1){
				cout << "请输入联系人电话:";
			}
			if (ch == 2){
				break;
			}
		}
	}

    cout << "请输入联系人生日(年 月 日):";
    while (!(cin >> y >> m >> d) || !Date::isValid(y,m,d)){
    	cin.clear();
    	CleanBuffer();
    	cout << "输入的日期不合法，请确认日期存在并且格式合法" << endl;
    	cout << "示范格式：2000 1 1" << endl;
    	cout << "请输入联系人生日(年 月 日):";
	}
	
    CleanBuffer();

    cout << "请输入联系人所在城市:";
    getline(cin, city);

    cout << "请输入联系人详细住址:";
    getline(cin, detail);

    if (typeChoice == 1) {
        Data[pos] = new Contact(name, telephone, Date(y, m, d), Address(city, detail));
    }
    else if (typeChoice == 2) {
        string studentId;
        string school;
        string major;

        cout << "请输入学号:";
        getline(cin, studentId);
        
        cout << "请输入学校:";
        getline(cin, school);

        cout << "请输入专业:";
        getline(cin, major);

        Data[pos] = new StudentContact(name, telephone, Date(y, m, d),
                                       Address(city, detail),
                                       studentId, school, major);
    }
    else if (typeChoice == 3) {
        string teacherId;
        string school;
        string department;
        string title;

        cout << "请输入工号:";
        getline(cin, teacherId);

        cout << "请输入学校:";
        getline(cin, school);

        cout << "请输入系部:";
        getline(cin, department);

        cout << "请输入职称:";
        getline(cin, title);

        Data[pos] = new TeacherContact(name, telephone, Date(y, m, d),
                                       Address(city, detail),
                                       teacherId, school, department, title);
    }
    cout << "\n*联系人添加成功!\n";
}

///==================== 添加联系人子菜单 ====================
int ContactBook::AddPersonList() {
    int choice = 1;
    while (choice != 0) {
        cout << "\n------------添加联系人------------\n";
        cout << "\n1.手动添加联系人（可选择类型）";
        cout << "\n2.导入csv文件(维护中，暂不可用)";
        cout << "\n3.导入加密csv文件(维护中，暂不可用)";
        cout << "\n0.返回上一步\n";
        cout << "\nMake your choice :";

        if ((cin >> choice) && choice >= 0 && choice <= 3) {
            CleanBuffer();
            switch (choice) {
                case 1:
                    AddPersonByHand();
                    break;
                case 2:
                    cout << endl << "说了这个功能还在维护！" << endl;
                    break;
                case 3:
                    cout << endl << "说了这个功能还在维护！" << endl;
                    break;
            }
        }
        else {
            cin.clear();
            CleanBuffer();
            cout << "\n*不是一个有效的数字!\n\n";
            choice = 1;
        }
    }
    return 0;
}

///==================== 联系人详情/修改菜单（三级）====================
int ContactBook::ThirdList(int Idx) {
    string type = Data[Idx]->getType();

    cout << "\n请选择要修改的项:" << endl;
    cout << "1.姓名  2.电话  3.城市  4.详细地址  5.生日";
    if (type == "学生") {
        cout << "  6.学号  7.学校  8.专业";
    }
    else if (type == "教师") {
        cout << "  6.工号  7.学校  8.系部  9.职称";
    }
    cout << endl;
    cout << "Make your choice :";

    int choice = 0;
    cin >> choice;
    CleanBuffer();

    if (choice == 5) {
        int y, m, d;
        cout << "请输入联系人生日(年 月 日):";
        while (!(cin >> y >> m >> d) || !Date::isValid(y,m,d)){
    		cin.clear();
    		CleanBuffer();
    		cout << "输入的日期不合法，请确认日期存在并且格式合法" << endl;
    		cout << "示范格式：2000 1 1" << endl;
    		cout << "请输入联系人生日(年 月 日):";
		}
        CleanBuffer();
        Data[Idx]->setBirthday(y,m,d);
        cout << "*修改成功！\n";
        return 0;
    }

    // 子类特有字段
    if (type == "学生" && (choice == 6 || choice == 7 || choice == 8)) {
        string input;
        cout << "请输入修改后的值:";
        getline(cin, input);
        Data[Idx]->setDescend( choice , input );
        cout << "*修改成功！\n";
        return 0;
    }

    if (type == "教师" && (choice == 6 || choice == 7 || choice == 8 || choice == 9)) {
        string input;
        cout << "请输入修改后的值:";
        getline(cin, input);
        Data[Idx]->setDescend( choice , input );
        cout << "*修改成功！\n";
        return 0;
    }

    // 基类字段（1-4）
    if (choice >= 1 && choice <= 4) {
        string input;
	    if (choice == 2) {
	    	cout << "请输入联系人电话:";
    		while (getline(cin, input)) {
    			if (input.size() == 11){
    				if (input[0] != '1'){
    					cout << "不合法的电话号码，请重新输入：" ;
					}
                    else {
                        break;
                    }
				}
				else {
					cout << "您输入的是一个非常用的电话号码，确认要继续吗？" << endl;
					cout << "1.重新输入" << endl;
					cout << "2.确认继续" << endl;
					cout << "Make your choice :";
					int ch = 0;
					while (!(cin >> ch) || ((ch < 1) || (ch > 2)) ){
						cin.clear();
						CleanBuffer();
						cout << "不合法的选择，请重新输入：";
					}
					CleanBuffer();
					if (ch == 1){
						cout << "请输入联系人电话:";
					}
					if (ch == 2){
						break;
					}
				}
			}
		}
        else {
        	cout << "请输入修改后的值:";
      		getline(cin, input);
		}

        switch (choice) {
            case 1:
                Data[Idx]->setName( input );
                break;
            case 2:
                Data[Idx]->setTelephone( input );
                break;
            case 3:
                Data[Idx]->setAddressCity( input );
                break;
            case 4:
                Data[Idx]->setAddressDetail( input );
                break;
        }
        cout << "*修改成功！\n";
        return 0;
    }

    cout << "*无效的选择！\n";
    return 0;
}

///==================== 联系人详情（二级）====================
int ContactBook::SecondList(int idx) {
    int choice = 1;
    while (choice != 0) {
        cout << "\n------------联系人详情------------\n\n";

        Data[idx]->show();

        // 基础修改选项
        cout << "\n1.更多修改选项";
        cout << "\n9.删除该联系人所有信息";
        cout << "\n0.返回上一步\n";
        cout << "\nMake your choice :";

        if ((cin >> choice) && ((choice >= 0 && choice <= 1) || choice == 9)) {
            CleanBuffer();
            if (choice == 9) {
                delete Data[idx];
                Data[idx] = nullptr;
                cout << "*联系人信息已删除！\n";
                return 0;
            }
            if (choice == 1) {
                ThirdList(idx);
            }
        }
        else {
            cin.clear();
            CleanBuffer();
            cout << "\n*不是一个有效的数字!\n\n";
            choice = 1;
        }
    }
    return 0;
}

///==================== 搜索联系人 ====================
int ContactBook::Search(int c) {
    string input;
    int found = 0;
    int choice = -1;

    cout << "\n请输入要搜索联系人的姓名(直接键入回车可取消搜索):";
    getline(cin, input);

    if (input.empty()) {
        cout << "\n*已取消搜索\n";
        return 0;
    }

    cout << "\n--------搜索结果---------\n";
    for (int i = 0; i < Data.size(); i = i + 1) {
        if (c == 1){
        	if (Data[i] != nullptr && Data[i]->isFindName(input)) {
            cout << i + 1 << ".[" << Data[i]->getType() << "] " << Data[i]->getName()  << ' ' << Data[i]->getTelephone() << endl; 
            found = 1;
        	}
		}
		else {
			if (Data[i] != nullptr && Data[i]->isFindTelephone(input)) {
            cout << i + 1 << ".[" << Data[i]->getType() << "] " << Data[i]->getName()  << ' ' << Data[i]->getTelephone() << endl; 
            found = 1;
        }
		}
    }

    if (found == 0) {
        cout << "\n*未找到相关联系人......\n";
        return 0;
    }

    cout << "\n输入编号查看具体信息,输入0返回上一步:";
    if ((cin >> choice) && choice > 0 && choice <= Data.size()) {
        CleanBuffer();
        if (Data[choice - 1] != nullptr) {
            SecondList(choice - 1);
        }
        else {
            cout << "\n*该位置没有联系人！\n";
        }
    }
    else if (choice == 0) {
        cin.clear();
        CleanBuffer();
        return 0;
    }
    else {
        cin.clear();
        CleanBuffer();
        cout << "\n*不是一个有效的数字!\n";
        return 0;
    }
    return 0;
}

///==================== 联系人列表（一级，含分类筛选）====================
int ContactBook::FirstList() {
    int choice = 1;
    int paper = 0;
    int filter = 0; // 0=全部, 1=普通, 2=学生, 3=教师

    while (choice != 0) {
        // 构建筛选后的索引列表
        vector<int> filteredIdx;
        for (int i = 0; i < Data.size(); i++) {
            if (Data[i] != nullptr) {
                if (filter == 0
                    || (filter == 1 && Data[i]->getType() == "普通")
                    || (filter == 2 && Data[i]->getType() == "学生")
                    || (filter == 3 && Data[i]->getType() == "教师")) {
                    filteredIdx.push_back(i);
                }
            }
        }

        cout << "\n------------联系人列表----------\n";
        cout << "筛选：0.全部 1.普通 2.学生 3.教师 | 当前：";
        switch (filter) {
            case 0: cout << "全部"; break;
            case 1: cout << "普通"; break;
            case 2: cout << "学生"; break;
            case 3: cout << "教师"; break;
        }
        cout << "\n\n";

        for (int i = 0; i < 6; i++) {
            int pos = i + paper * 6;
            if (pos < filteredIdx.size()) {
                int realIdx = filteredIdx[pos];
                cout << i + 1 << ".[" << Data[realIdx]->getType() << "] "
                     << Data[realIdx]->getName() << "\n";
            }
            else if (pos < (paper + 1) * 6) {
                cout << i + 1 << ".空\n";
            }
        }

        int totalPages = filteredIdx.size() / 6;
        if (filteredIdx.size() % 6 != 0) {
            totalPages++;
        }
        if (totalPages == 0) totalPages = 1;
        cout << "\n----------第 " << paper + 1 << "/" << totalPages << " 页-----------\n";
        cout << "7.下一页  8.上一页  9.搜索  10.切换筛选  0.返回\n";
        cout << "输入对应数字可查看详细信息\n";
        cout << "\nMake your choice :";

        if (cin >> choice) {
            CleanBuffer();
            if (choice >= 0 && choice <= 10) {
                if (choice >= 1 && choice <= 6) {
                    int pos = (paper * 6) + choice - 1;
                    if (pos >= 0 && pos < filteredIdx.size()) {
                        int realIdx = filteredIdx[pos];
                        SecondList(realIdx);
                    }
                    else if (pos < (paper + 1) * 6) {
                        cout << "\n*该位置暂无联系人！\n";
                    }
                }
                switch (choice) {
                    case 7:
                        if (paper < totalPages - 1) {
                            paper = paper + 1;
                        }
                        else {
                            cout << "\n*没有下一页了\n";
                        }
                        break;
                    case 8:
                        if (paper > 0) {
                            paper = paper - 1;
                        }
                        else {
                            cout << "\n*没有上一页了\n";
                        }
                        break;
                    case 9:{
                    	cout << "1.按名字搜索" << endl;
                    	cout << "2.按手机号搜索" << endl;
                    	int u = 0;
                    	while (!(cin >> u) || ((u < 1) || (u > 2))){
                    		cin.clear();
                    		CleanBuffer();
                    		cout << "输入不合法，请重新输入：";
						}
						CleanBuffer();
						if (u == 1){
							Search(1);
						}
						else {
							Search(2);
						}
						break;
					}
                    case 10:
                        filter = (filter + 1) % 4;
                        paper = 0;
                        break;
                    case 0:
                        choice = 0;
                        break;
                }
            }
            else {
                cout << "\n*这不是一个有效的数字!\n\n";
                choice = 1;
            }
        }
        else {
            cin.clear();
            CleanBuffer();
            cout << "\n*这不是一个有效的数字!\n\n";
            choice = 1;
        }
    }
    return 0;
}

///===================生日提醒======================
//这个函数的实现非常之诡异，非常之bt
//不是哥们我废了老半天的劲写了这个逻辑
//然后发现效率比起一步步的addone好像没有多大提升......？
void ContactBook::BirthRemindList(){
	int y = 0,m = 0,d = 0;
	cout << "请输入当前日期(年 月 日)：";
	while (!(cin >> y >> m >> d) || !Date::isValid(y,m,d)){
    	cin.clear();
    	CleanBuffer();
    	cout << "输入的日期不合法，请确认日期存在并且格式合法" << endl;
    	cout << "示范格式：2000 1 1" << endl;
    	cout << "请输入当前日期(年 月 日):";
	}
	CleanBuffer();
	int n = 0;
	const int MaxRemindDays = 3650;
		cout << "请输入提醒天数(最大" << MaxRemindDays << ")：";
	while (!(cin >> n) || n < 0 || n > MaxRemindDays){
		cin.clear();
    	CleanBuffer();
    	if (n > MaxRemindDays)
	    		cout << "提醒天数超出上限，请输入0~" << MaxRemindDays << "之间的整数：";
	    	else
	    		cout << "输入的天数不合法，请重新输入：" << endl;
	}
	CleanBuffer();
	Date nowD = {y,m,d},targetD = {y,m,d};
    int nON = nowD.getOrdinalNum(),tON = 0,overON = 0;
    if (nowD.isLeapYear() == true){
        overON = nON + n - 366;
    }
    else {
        overON = nON + n - 365;
    }

    int count = 0;

    cout << "\n========== 未来 " << n << " 天内生日提醒 ==========\n" << endl;

    for (int i = 0;i < Data.size();i ++){

        if (Data[i] == nullptr) continue;

        targetD.set(y+1,Data[i]->getBirthday().getMonth(),Data[i]->getBirthday().getDay());
        tON = targetD.getOrdinalNum();

        //当对象日期不处在2.29时
        if (!(targetD.getMonth() == 2 && targetD.getDay() == 29)){
            if (tON >= nON){
                //这一段用来校正tON，在闰年平年交替时会导致tON+1和tON-1
                //今年是平年，明年是闰年时
                if (targetD.isLeapYear()){
                    if (targetD.getMonth() > 2){
                        tON--;
                    }
                }
                //今年是闰年，明年是平年时
                if (nowD.isLeapYear()){
                    if (!targetD.isLeapYear()){
                        if (targetD.getMonth() > 2){
                            tON++;
                        }
                    }
                }
                //以上一段用来校正tON，在闰年平年交替时会导致tON+1和tON-1
                if (nON + n >= tON){
                    cout << "  [" << Data[i]->getType() << "] "
                         << Data[i]->getName() << "  →  ";
                    Data[i]->showBirthday();
                    cout << endl;
                    count++;
                }
            }
            else {
                if (overON >= tON){
                    cout << "  [" << Data[i]->getType() << "] "
                         << Data[i]->getName() << "  →  ";
                    Data[i]->showBirthday();
                    cout << endl;
                    count++;
                }
            }
        }
        //当对象日期处在2.29时
        else {
            if (nowD.isLeapYear()){
                if (nowD.getMonth() > 2){
                    if (n >= 60 + 365*3 - nON + 366){
                        cout << "  [" << Data[i]->getType() << "] "
                             << Data[i]->getName() << "  →  ";
                        Data[i]->showBirthday();
                        cout << " (特殊)" << endl;
                        count++;
                    }
                }
                else {
                    if (n >= 60 - nON){
                        cout << "  [" << Data[i]->getType() << "] "
                             << Data[i]->getName() << "  →  ";
                        Data[i]->showBirthday();
                        cout << " (特殊)" << endl;
                        count++;
                    }
                }
            }
            else {
                int nextLeapYear = nowD.getYear();
                while (!Date::isValid(nextLeapYear, 2, 29)){
                    nextLeapYear++;
                }
                int differ = nextLeapYear - nowD.getYear();
                if (n >= differ*365 + 60 - nON){
                    cout << "  [" << Data[i]->getType() << "] "
                         << Data[i]->getName() << "  →  ";
                    Data[i]->showBirthday();
                    cout << " (特殊)" << endl;
                    count++;
                }
            }
        }
    }

    //如果没有发现联系人补提示
    if (count == 0){
        cout << "未检索到在天数内过生日的联系人。" << endl;
    }
    else {
        cout << "\n--- 共 " << count << " 位联系人 ---\n" << endl;
    }

}


//导入与导出
void ContactBook::InAndOut(){
    cout << "1.导出通讯录" << endl;
    cout << "2.导入通讯录" << endl;
    cout << "Make your choice：";
    int n = 0;
    while (!(cin >> n) || n < 1 || n > 2) {
        cin.clear();
        CleanBuffer();
        cout << "不合法的输入，请重试：";
    }
    CleanBuffer();
    if (n == 1){
        cout << "1.默认路径导出" << endl;
        cout << "2.自定义路径导出" << endl;
        cout << "Make your choice：";
        int choice = 0;
        while (!(cin >> choice) || choice < 1 || choice > 2) {
            cin.clear();
            CleanBuffer();
            cout << "不合法的输入，请重试：";
        }
        CleanBuffer();

        string filename = "contacts.csv";
        if (choice == 2){
            cout << "请输入导出路径及文件名(如 D:\\通讯录.csv)：";
            getline(cin, filename);
            if (filename.empty()){
                cout << "*未输入路径，使用默认文件名。\n";
                filename = "contacts.csv";
            }
        }

        ofstream fout(filename);
        if (!fout.is_open() ){
            cout << "导出失败，请确认目标文件处于关闭状态";
            return;
        }
        fout << "\xEF\xBB\xBF";
        fout << "编号,姓名,电话号码,联系人类型,生日,地址,学号,工号,学校,专业,系部,职称" << endl;
        for (int i = 0;i < Data.size();i ++){
            if (Data[i] == nullptr){
                continue;
            }
            fout << Data[i]->getId() << ',';
            fout << Data[i]->getName() << ',';
            fout << Data[i]->getTelephone() << ',';
            fout << Data[i]->getType() << ',';
            fout << Data[i]->getBirthday().getYear() << '-';
            fout << Data[i]->getBirthday().getMonth() << '-';
            fout << Data[i]->getBirthday().getDay() << ',';
            fout << Data[i]->getAddress().getCity() << '-';
            fout << Data[i]->getAddress().getDetail() << ',';
            //c++风格，把Data[i]尝试转换为StudentContact*类型,然后新建指针s指向他
            //原c风格 auto* s = (StudentContact*)Data[i]; 转错了会崩溃
            //在建议下更改为c++风格,在出错时只会返回nullptr
            auto* stu = dynamic_cast<StudentContact*>(Data[i]);
            auto* tea = dynamic_cast<TeacherContact*>(Data[i]);

            // 学号列
            if (stu != nullptr) fout << stu->getStudentId();
            fout << ',';
            // 工号列
            if (tea != nullptr) fout << tea->getTeacherId();
            fout << ',';
            // 学校列
            if (stu != nullptr) fout << stu->getSchool();
            else if (tea != nullptr) fout << tea->getSchool();
            fout << ',';
            // 专业列
            if (stu != nullptr) fout << stu->getMajor();
            fout << ',';
            // 系部列
            if (tea != nullptr) fout << tea->getDepartment();
            fout << ',';
            // 职称列
            if (tea != nullptr) fout << tea->getTitle();
            fout << endl;
        }
        fout.close();
        if (fout.fail()){
            cout << "导出失败，在写入过程中出现错误";
        }
        else {
            cout << "导出成功！";
        }
    }
    else {
        cout << "请输入目标文件路径(如 D:\\通讯录.csv)：";
        string path = "p";
        getline(cin, path);
        ifstream fin(path);
        if (!fin.is_open()){
            cout << "导入失败，请确认输入了正确的路径或者文件存在";
            return;
        }
        fin >> ws;
        if (fin.eof()){
            cout << "导入失败，文件为空";
            return;
        }
        cout << "正在导入......" << endl;
        string firstLine, line;
        getline(fin, firstLine);
        int lineNum = 1, success = 0, skipped = 0;
        while (getline(fin, line)) {
            lineNum++;
            if (line.empty()) continue;

            stringstream ss(line);
            string id, name, phone, type, birthStr, addr;
            string extra1, extra2, extra3, extra4, extra5, extra6;
            getline(ss, id, ',');
            getline(ss, name, ',');
            getline(ss, phone, ',');
            getline(ss, type, ',');
            getline(ss, birthStr, ',');
            getline(ss, addr, ',');
            getline(ss, extra1, ',');
            getline(ss, extra2, ',');
            getline(ss, extra3, ',');
            getline(ss, extra4, ',');
            getline(ss, extra5, ',');
            getline(ss, extra6, ',');

            if (id.empty() || name.empty() || phone.empty() || type.empty() || birthStr.empty()) {
                cout << "在第" << lineNum << "行发现错误：基础字段缺失。已跳过\n";
                skipped++;
                continue;
            }

            //编号校对，检查是否全为数字
            int idNum;
            bool isDigit = !id.empty();
            for (int j = 0; j < id.size(); j++) {
                if (id[j] < '0' || id[j] > '9') {
                    isDigit = false;
                    break;
                }
            }
            if (!isDigit) {
                cout << "在第" << lineNum << "行发现错误：编号格式错误。已跳过\n";
                skipped++;
                continue;
            }
            else {
                idNum = stoi(id);
            }

            // 编号重复检查
            bool isDuplicate = false;
            for (int j = 0; j < Data.size(); j++) {
                if (Data[j] != nullptr && Data[j]->getId() == idNum) {
                    isDuplicate = true;
                    break;
                }
            }
            if (isDuplicate) {
                cout << "在第" << lineNum << "行发现错误：编号 " << idNum << " 已存在。已跳过\n";
                skipped++;
                continue;
            }

            // 手机号校验
            if (phone.size() != 11 || phone[0] != '1') {
                cout << "在第" << lineNum << "行发现错误：手机号不合法。已跳过\n";
                skipped++;
                continue;
            }

            //生日转换并检查合法性
            int y,m,d;
            stringstream dateStr(birthStr);
            string yStr,mStr,dStr;
            getline(dateStr, yStr, '-');
            getline(dateStr, mStr, '-');
            getline(dateStr, dStr, '-');
            bool dateLegal = true;
            if (yStr.empty() || mStr.empty() || dStr.empty()) {
                dateLegal = false;
            }
            if (dateLegal){
                for (int i = 0;i < yStr.size();i ++){
                    if (yStr[i] < '0' || yStr[i] > '9') dateLegal = false;
                }
                for (int i = 0;i < mStr.size();i ++){
                    if (mStr[i] < '0' || mStr[i] > '9') dateLegal = false;
                }
                for (int i = 0;i < dStr.size();i ++){
                    if (dStr[i] < '0' || dStr[i] > '9') dateLegal = false;
                }
            }
            if (dateLegal){
                y = stoi(yStr);
                m = stoi(mStr);
                d = stoi(dStr);
                if (!Date::isValid(y,m,d)){
                    dateLegal = false;
                }
            }
            if (!dateLegal){
                cout << "在第" << lineNum << "行发现错误：日期不合法。已跳过\n";
                skipped++;
                continue;
            }

            //地址拆分
            stringstream addrStr(addr);
            string city,detail;
            getline(addrStr, city, '-');
            getline(addrStr, detail);

            //开始传进数据内部
            int pos = 0;
            pos = FindEmptyPos();
            if (pos == -1) {
                cout << "在第" << lineNum << "行发现错误：联系人数量达到上限。已跳过\n";
                skipped++;
                continue;
            }
            if (type == "普通"){
                Data[pos] = new Contact(name, phone, Date(y,m,d), Address(city, detail));
                Data[pos]->setId(idNum);
                if (idNum >= Contact::getNextId())
                    Contact::setNextId(idNum + 1);
                success++;
            }
            else if (type == "学生"){
                Data[pos] = new StudentContact(name, phone, Date(y,m,d), Address(city, detail), extra1, extra3, extra4);
                Data[pos]->setId(idNum);
                if (idNum >= Contact::getNextId())
                    Contact::setNextId(idNum + 1);
                success++;
            }
            else if (type == "教师"){
                Data[pos] = new TeacherContact(  name, phone, Date(y,m,d), Address(city, detail), extra2, extra3, extra5, extra6);
                Data[pos]->setId(idNum);
                if (idNum >= Contact::getNextId())
                    Contact::setNextId(idNum + 1);
                success++;
            }
            else {
                cout << "在第" << lineNum << "行发现错误：未识别的联系人类型。已跳过\n";
                skipped++;
            }
        }
        fin.close();
        cout << "\n导入完成！成功 " << success << " 条，跳过 " << skipped << " 条。\n";
    }
}
 

///==================== 统计联系人信息 ====================
void ContactBook::Statistics(){
    int countNormal = 0, countStudent = 0, countTeacher = 0;
    int countByMonth[13] = {0};
    int total = 0;

    for (int i = 0; i < Data.size(); i++){
        if (Data[i] == nullptr) continue;

        total++;
        string t = Data[i]->getType();
        if (t == "普通")      countNormal++;
        else if (t == "学生") countStudent++;
        else if (t == "教师") countTeacher++;

        int m = Data[i]->getBirthday().getMonth();
        if (m >= 1 && m <= 12)
            countByMonth[m]++;
    }

    cout << "\n========== 联系人统计 ==========\n" << endl;
    cout << "当前联系人总数：" << total << "\n" << endl;

    cout << "按类型统计：" << endl;
    cout << "  普通联系人：" << countNormal << " 人" << endl;
    cout << "  学生联系人：" << countStudent << " 人" << endl;
    cout << "  教师联系人：" << countTeacher << " 人" << endl;

    cout << "\n按出生月份统计：" << endl;
    for (int m = 1; m <= 12; m++){
        if (countByMonth[m] > 0)
            cout << "  " << m << "月：" << countByMonth[m] << " 人" << endl;
    }
    cout << endl;
}

///==================== 主函数 ====================
int main() {
    SetConsoleCP(65001);
    SetConsoleOutputCP(65001);
    ContactBook Book;
    Book.NewLoad();
    Book.InformationLoad();

    int choice = 1;
    while (choice != 0) {
        cout << "\n";
        cout << "-------------通讯录-------------\n";
        cout << "\n1.查找联系人";
        cout << "\n2.添加联系人";
        cout << "\n3.导出与导入联系人";
        cout << "\n4.排序联系人(将按照姓氏拼音排序)";
        cout << "\n5.生日提醒";
        cout << "\n6.统计联系人";
        cout << "\n0.退出\n";
        cout << "\nMake your choice :";

        if ((cin >> choice) && choice >= 0 && choice <= 6) {
            CleanBuffer();
            switch (choice) {
                case 1:
                    Book.FirstList();
                    break;
                case 2:
                    Book.AddPersonList();
                    break;
                case 3:
                    Book.InAndOut();
                    break;
                case 4:
                    Book.SortList();
                    break;
                case 5:
                	Book.BirthRemindList();
                	break;
                case 6:
                	Book.Statistics();
                	break;
            }
        }
        else {
            cin.clear();
            CleanBuffer();
            cout << "\n*这不是一个有效的数字!\n";
        }
    }

    return 0;
}
